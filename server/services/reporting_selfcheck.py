"""Does the Reporting page's arithmetic hold, and is the data behind it intact?

Two different failures produce the same screen. A measure that reads 0 might mean
"we measured and the answer is zero", or it might mean "the column those rows are
derived from is empty, so every measure built on it is structurally 0". A
dashboard that cannot tell those apart will quietly mislead for weeks — that is
this project's first rule, applied to the numbers a manager acts on.

So this module answers two questions and NEVER merges them:

  * INVARIANTS — is the query engine self-consistent? Grouping the same rows two
    ways must give the same totals; a single-valued dimension must partition the
    cohort exactly; a percentage must lie in 0..100. A failure here is a BUG.
  * DATA HEALTH — do the columns the measures rest on actually carry values? A
    failure here is not a bug; it is a fact about the database, and the honest
    response is to say which measures are therefore unreadable rather than to
    print a confident 0.

Read-only. It runs the same `records()`/`run_query()` the page does, so it checks
the shipped path rather than a copy of it.
"""
from __future__ import annotations

from datetime import datetime

from server.services.reporting_query import (
    DIMENSIONS, DIM_BY_KEY, MEASURES, records, run_query, values_of)

# A measure is only as trustworthy as the field it is derived from. When a source
# field is empty across every row in scope, the measure is not "zero" — it is
# unanswerable, and saying so is the whole point of this module.
MEASURE_SOURCES = {
    "solved":      ("solved",     "review status reaching 'sent'"),
    "solved_pct":  ("solved",     "review status reaching 'sent'"),
    "posted":      ("posted",     "the RCA's rca_posted_at timestamp"),
    "median_tts":  ("tts_hours",  "a finish timestamp (sent_at or closed_at)"),
    "traced_pct":  ("_traceable", "a booking id on the draft"),
    "avg_rating":  ("rating",     "the review's star rating"),
    "flags":       ("flag_count", "flags on the RCA"),
    "zendesk":     ("zd_count",   "linked Zendesk ticket ids"),
    "avg_issues":  ("issue_count", "the RCA's issue list"),
}


def _truthy(rec: dict, key: str) -> bool:
    """Whether this record actually carries a value for a measure's source."""
    if key == "_traceable":
        return rec.get("traceable") == "Traceable"
    v = rec.get(key)
    if v is None or v == "" or v == []:
        return False
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return v != 0
    return True


def invariants(recs: list[dict]) -> list[dict]:
    """Checks that must hold for ANY data. A failure is a defect in the engine.

    Each check reports what it compared, so a pass is evidence rather than a
    green tick — "ran and agreed" must be distinguishable from "did not run"."""
    out: list[dict] = []

    def add(name, ok, detail):
        out.append({"check": name, "ok": bool(ok), "detail": detail})

    total = len(recs)

    # 1. A single-valued dimension PARTITIONS the cohort: every row lands in
    #    exactly one bucket, so the buckets sum back to the total. This is the
    #    check that catches a row being dropped or double counted.
    for key in ("tier", "traceable", "status"):
        if key not in DIM_BY_KEY:
            continue
        res = run_query(recs, [key], ["count"], limit=10_000)
        summed = sum(r["values"]["count"] for r in res["rows"])
        add(f"{DIM_BY_KEY[key].label}: buckets sum to the cohort",
            summed == total,
            f"{summed} across {len(res['rows'])} buckets vs {total} reviews")

    # 2. Totals are recomputed over the cohort, never summed from the rows. For
    #    COUNT the two must agree; for a median or a percentage they need not,
    #    which is exactly why totals are not summed.
    res = run_query(recs, ["tier"], ["count"], limit=10_000)
    add("Count total equals the sum of its rows",
        res["totals"]["count"] == sum(r["values"]["count"] for r in res["rows"]),
        f"total={res['totals']['count']}")

    # 3. Grouping by a different dimension must not change the cohort size.
    a = run_query(recs, ["tier"], ["count"], limit=10_000)["totals"]["count"]
    b = run_query(recs, ["status"], ["count"], limit=10_000)["totals"]["count"]
    c = run_query(recs, [], ["count"])["totals"]["count"]
    add("The total is the same however it is grouped",
        a == b == c == total, f"by tier={a}, by status={b}, ungrouped={c}, rows={total}")

    # 4. The SAME query twice must give the same answer. This is the check that
    #    would have caught the Overview and the Explore disagreeing by one review.
    r1 = run_query(recs, ["tier"], ["count"], limit=10_000)
    r2 = run_query(recs, ["tier"], ["count", "solved"], limit=10_000)
    m1 = {tuple(r["key"]): r["values"]["count"] for r in r1["rows"]}
    m2 = {tuple(r["key"]): r["values"]["count"] for r in r2["rows"]}
    add("The same grouping is stable across queries", m1 == m2,
        f"{m1} vs {m2}")

    # 5. Percentages are shares, so they cannot leave 0..100. A cross-cohort
    #    ratio is how this project once shipped a "130%".
    bad = []
    for key in ("tier", "owner"):
        if key not in DIM_BY_KEY:
            continue
        res = run_query(recs, [key], ["solved_pct", "traced_pct"], limit=10_000)
        for r in res["rows"]:
            for mk, v in r["values"].items():
                if v is not None and not (0 <= v <= 100):
                    bad.append(f"{key}={r['key'][0]} {mk}={v}")
    add("Every percentage lies between 0 and 100", not bad,
        "; ".join(bad) if bad else "all shares in range")

    # 6. A solved review must carry a finish timestamp, or "solved" and "median
    #    time to send" are describing different populations.
    solved = [r for r in recs if r["solved"]]
    timed = [r for r in solved if r["tts_hours"] is not None]
    add("Solved reviews carry a finish timestamp",
        len(solved) == len(timed),
        f"{len(timed)} of {len(solved)} solved reviews are timed")

    # 7. Unnesting is declared whenever a multi-value field is grouped, because
    #    counts can then exceed the review total and the reader must be told.
    multi = next((d.key for d in DIMENSIONS if d.multi and d.key in DIM_BY_KEY), None)
    if multi:
        res = run_query(recs, [multi], ["count"], limit=10_000)
        add("A multi-value grouping declares itself", res["unnested"] == [multi],
            f"unnested={res['unnested']}")
    return out


def data_health(recs: list[dict]) -> list[dict]:
    """Per measure: can it be answered from this data at all?

    `state` is one of:
      * "ok"      — the source field carries values; the measure means something.
      * "empty"   — NO row in scope carries the source field. The measure will
                    read 0 (or "—") for structural reasons, not because the
                    answer is zero. This is the case a dashboard must never
                    present as a confident figure.
      * "partial" — some rows carry it; the measure describes only those.
    """
    out: list[dict] = []
    total = len(recs)
    if not total:
        # With no rows, "have == total" is 0 == 0 and EVERY measure would report
        # healthy — a vacuous green that reads exactly like a real one. There is
        # nothing to judge, and saying so is the only honest answer.
        return [{"measure": k, "label": v[0], "state": "unknown", "have": 0, "of": 0,
                 "note": "no reviews in scope — nothing to judge this measure on"}
                for k, v in MEASURES.items()]
    for mkey, (label, _fn) in MEASURES.items():
        src = MEASURE_SOURCES.get(mkey)
        if not src:
            out.append({"measure": mkey, "label": label, "state": "ok",
                        "have": total, "of": total,
                        "note": "counts rows, so it needs no source field"})
            continue
        field, human = src
        have = sum(1 for r in recs if _truthy(r, field))
        state = "ok" if have == total else ("empty" if have == 0 else "partial")
        note = {
            "ok": f"every review in scope has {human}",
            "partial": f"{total - have} of {total} reviews have no {human}",
            "empty": (f"NO review in scope has {human} — this measure reads 0/— "
                      f"because the data is absent, not because the answer is zero"),
        }[state]
        out.append({"measure": mkey, "label": label, "state": state,
                    "have": have, "of": total, "note": note})
    return out


def dimension_fill(recs: list[dict]) -> list[dict]:
    """How many reviews carry each dimension. A field nobody fills makes a small
    bar mean something very different from the same bar on a complete field."""
    total = len(recs)
    out = []
    for d in DIMENSIONS:
        have = sum(1 for r in recs if values_of(r, d.key))
        out.append({"key": d.key, "label": d.label, "view": d.view,
                    "have": have, "of": total,
                    "state": "ok" if have == total else ("empty" if have == 0 else "partial")})
    return out


def _excluded_test_rows(db, start, end) -> int:
    """How many reviews in this window carry a test-owner name — i.e., how many
    the reporting engine dropped as non-production. This is here rather than in
    the query engine because reporting must not COUNT them; a manager who sees
    "digest 5, dashboard 7" has to be able to see the two rows are the same
    two rows they always are."""
    from server.db import Review
    from sqlalchemy import func
    from server.services.reporting_query import TEST_OWNERS
    q = (db.query(Review)
           .filter(func.lower(func.trim(Review.picked_up_by)).in_(TEST_OWNERS)))
    if start is not None:
        q = q.filter(Review.received_at >= start)
    if end is not None:
        q = q.filter(Review.received_at < end)
    return q.count()


def dashboard_parity(db) -> dict:
    """Do the dashboard and Reporting agree on how many reviews exist?

    THE INVARIANT: the inbox (`/api/reviews`, now uncapped) shows every Review
    row. Reporting counts every Review row EXCEPT test-owner ones. So, all-time:

        dashboard_total  ==  reporting_total  +  excluded_test_rows

    This existed to catch the two ways they silently drifted: a `.limit(200)`
    on the inbox that hid older rows, and the test-owner exclusion. Both are
    legitimate reasons for two numbers — this states the equation so a reader
    can see the difference is exactly those rows and nothing else. `ok` False
    means an UNEXPLAINED gap: a row reporting drops for a reason nobody named,
    which is the bug this whole file exists to make impossible to ship silent."""
    from server.db import Review
    dashboard_total = db.query(Review).count()
    reporting_total = len(records(db, None, None))     # all-time, test rows excluded
    excluded = _excluded_test_rows(db, None, None)
    reconciled = reporting_total + excluded
    ok = (reconciled == dashboard_total)
    return {
        "ok": ok,
        "dashboard_total": dashboard_total,
        "reporting_total": reporting_total,
        "excluded_test_rows": excluded,
        "unexplained_gap": dashboard_total - reconciled,
        "note": ("dashboard = reporting + test-owner rows" if ok else
                 f"UNEXPLAINED: {dashboard_total} on the dashboard, but "
                 f"{reporting_total} in reporting + {excluded} test = {reconciled}. "
                 f"{dashboard_total - reconciled} review(s) vanish for no stated reason."),
    }


def run(db, date_from: datetime | None = None, date_to: datetime | None = None) -> dict:
    """The whole self-check over one window. Read-only.

    Also reports how many rows this window has that reporting EXCLUDES (test-
    owner rows), so the exclusion is visible on screen rather than a silent
    reason the dashboard's headline and the digest disagree, and a
    dashboard-vs-reporting parity check so an unexplained count gap is loud."""
    recs = records(db, date_from, date_to)
    excluded = _excluded_test_rows(db, date_from, date_to)
    parity = dashboard_parity(db)
    inv = invariants(recs) if recs else []
    health = data_health(recs) if recs else []
    empties = [h for h in health if h["state"] == "empty"]
    failed = [i for i in inv if not i["ok"]]

    if not recs:
        verdict = "no reviews in this window — nothing to check"
    elif failed:
        verdict = (f"{len(failed)} INVARIANT FAILED — the numbers on the "
                   f"Reporting page cannot be trusted until this is fixed")
    elif empties:
        verdict = (f"arithmetic is consistent, but {len(empties)} measure(s) have "
                   f"no data behind them in this window and will read 0 or — for "
                   f"that reason, not because the answer is zero")
    else:
        verdict = "arithmetic is consistent and every measure has data behind it"

    # An unexplained dashboard/reporting gap is a hard failure of its own — the
    # numbers cannot be trusted while two views of "how many reviews" disagree
    # for a reason nobody named.
    if not parity["ok"]:
        verdict = (f"DASHBOARD/REPORTING MISMATCH — {parity['note']} "
                   + ("" if not failed else "Also: " + verdict))

    return {
        "reviews": len(recs),
        "excluded_test_rows": excluded,
        "dashboard_parity": parity,
        "verdict": verdict,
        "invariants_failed": len(failed),
        "invariants": inv,
        "measures": health,
        "dimensions": dimension_fill(recs) if recs else [],
    }
