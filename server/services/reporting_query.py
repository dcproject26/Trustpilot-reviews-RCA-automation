"""Read-only analytics over the reviews, behind the Reporting page.

Every review is projected through `sheet_export.row_for` — the SAME projection
the CSV export uses — and only then reduced to the analytics record this module
groups and aggregates. That reuse is deliberate: a second projection would be a
second definition of "what vendor means", and the dashboard and the export would
drift apart the first time one of them was edited.

Three layers, so the arithmetic is testable without a database:
  * `project(review, draft)`   — one review -> one analytics record (pure).
  * `run_query(records, ...)`  — records -> grouped rows + totals (pure).
  * `records(db, start, end)`  — the DB-facing glue.

NOTHING here writes, runs a BigQuery lookup, or calls a model. It reads rows that
other runs already wrote.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

# Accounts that are not real associates. A review picked up by one of these is
# TEST DATA and is excluded from EVERY reporting measure — received, solved,
# pending, tier, per-person, and the Reporting page's records. The dashboard
# still shows these rows so an auditor can see them; the numbers a manager
# reads must not include them, because a "5 pending" that is really 3 real +
# 2 test misinforms the person who has to act on it.
#
# One earlier decision kept the row and dropped only the person attribution,
# to avoid losing one historic 1-star complaint that had been closed from a
# test account. That trade has been reversed: the loss of that one row is
# named in the digest via `selfcheck`'s excluded-test-rows count, so the
# exclusion is visible rather than silent.
TEST_OWNERS = {"test", "qa", "testing", "demo"}
UNASSIGNED = "Unassigned"


def is_test_row(review) -> bool:
    """True if this review was worked from a test account. The owner is the
    only signal we trust — matching on author or body would be guesswork and
    would drop real complaints that happen to say "test" in them."""
    name = str(getattr(review, "picked_up_by", "") or "").strip().lower()
    return bool(name) and name in TEST_OWNERS


def not_test_row_clause(Review):
    """The SQL form of `is_test_row(r) is False`, for adding to a query with
    .filter(). NULL picked_up_by is production (the review is unassigned, not
    test) and a non-test name is production. Written as NOT(picked_up_by IN
    …) so a NULL name evaluates to NULL and the outer NOT/IS TRUE both drop
    it into production — the plain form `IS NULL OR ... NOT IN` reads correct
    but IS NOT the same at the query planner if any join changes nullability.
    Using `func.lower` matches the Python predicate's case-insensitivity."""
    from sqlalchemy import func, not_, or_
    return or_(Review.picked_up_by.is_(None),
               not_(func.lower(func.trim(Review.picked_up_by)).in_(TEST_OWNERS)))

# What a grouped row is called when the review has no value for that field. It is
# NOT the same as a zero count and is never silently dropped — `run_query` also
# reports an `unset` count per dimension.
UNSET = "(not set)"


@dataclass(frozen=True)
class Dim:
    """One groupable field. `multi` means a review can hold several values, which
    are unnested for grouping (so counts can exceed the review total)."""
    key: str
    view: str
    label: str
    multi: bool = False


# The registry the client renders its field picker from — grouped by the RCA
# card's own sections so the list reads the way the team already reads a review.
# Fields the export leaves EMPTY are deliberately absent: pax, booked_on,
# match_confidence, vid_name, sp_notes, dss_ref, issue_answers. Free-text fields
# (review text, findings, responses, notes) are not dimensions either — grouping
# by a 600-character paragraph yields one bucket per review, not a statistic.
DIMENSIONS: list[Dim] = [
    Dim("date",              "Review",               "Date"),
    Dim("rating",            "Review",               "Rating"),
    Dim("language",          "Review",               "Language"),
    Dim("status",            "Review",               "Status"),
    Dim("experience",        "Booking details",      "Experience / product"),
    Dim("vendor",            "Booking details",      "Vendor / supply partner"),
    Dim("tgid",              "Booking details",      "TGID"),
    Dim("fulfilment_type",   "Booking details",      "Fulfilment type"),
    Dim("booking_status",    "Booking details",      "Booking status"),
    Dim("tier",              "Matching",             "Match tier"),
    Dim("traceable",         "Matching",             "Booking traced"),
    Dim("match_method",      "Matching",             "Match method"),
    Dim("l1",                "What went wrong",      "L1 category"),
    Dim("l2",                "What went wrong",      "L2 category"),
    Dim("sub_themes",        "What went wrong",      "Sub-theme", True),
    Dim("scenarios",         "What went wrong",      "Scenario", True),
    Dim("overlay_scenarios", "What went wrong",      "Overlay scenario", True),
    Dim("claim_accuracy",    "What went wrong",      "Claim accuracy", True),
    Dim("resolution_given",  "Resolution & outcome", "Resolution given"),
    Dim("takedown",          "Resolution & outcome", "Review takedown"),
    Dim("outcome_category",  "Resolution & outcome", "Outcome category"),
    Dim("dss_followed",      "Resolution & outcome", "DSS followed"),
    Dim("owner",             "Handling",             "Picked up by"),
    Dim("sent_route",        "Handling",             "Sent route"),
    Dim("close_reason",      "Handling",             "Close reason"),
    Dim("prompt_version",    "Handling",             "RCA prompt version"),
]
DIM_BY_KEY = {d.key: d for d in DIMENSIONS}
VIEWS = list(dict.fromkeys(d.view for d in DIMENSIONS))


# ── measures ────────────────────────────────────────────────────────────────

def _median(vals):
    if not vals:
        return None
    s = sorted(vals)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _mean(vals):
    return (sum(vals) / len(vals)) if vals else None


def _pct(n, d):
    return (n / d * 100) if d else None


def _tts_values(rs):
    return [r["tts_hours"] for r in rs if r["tts_hours"] is not None]


# Time to send is reported as a MEDIAN, not a mean. The real spread runs ~2h to
# ~650h, because a backlog cleared late drags the mean to roughly six times the
# typical case; a mean here would misreport how fast the team actually is.
MEASURES = {
    "count":       ("Count of reviews",        lambda rs: len(rs)),
    "solved":      ("Solved",                  lambda rs: sum(1 for r in rs if r["solved"])),
    "solved_pct":  ("Solved %",                lambda rs: _pct(sum(1 for r in rs if r["solved"]), len(rs))),
    "traced_pct":  ("Booking traced %",        lambda rs: _pct(sum(1 for r in rs if r["traceable"] == "Traceable"), len(rs))),
    "untraceable": ("Untraceable",             lambda rs: sum(1 for r in rs if r["tier"] == "Untraceable")),
    "posted":      ("RCAs posted",             lambda rs: sum(1 for r in rs if r["posted"])),
    "avg_rating":  ("Avg rating",              lambda rs: _mean([r["rating"] for r in rs if r["rating"]])),
    "median_tts":  ("Median time to send (h)", lambda rs: _median(_tts_values(rs))),
    "flags":       ("Flags raised",            lambda rs: sum(r["flag_count"] for r in rs)),
    "zendesk":     ("Zendesk tickets",         lambda rs: sum(r["zd_count"] for r in rs)),
    "avg_issues":  ("Avg issues / review",     lambda rs: _mean([r["issue_count"] for r in rs])),
}


# ── projection ──────────────────────────────────────────────────────────────

def _declared_untraceable(close_reason: str, match_method: str) -> bool:
    """A person saying "no booking": closed with the untraceable reason, or marked
    untraceable off the shortlist. Either overrides a stale tentative match_tier."""
    return ((close_reason or "").strip().lower().startswith("untraceable")
            or (match_method or "") == "Marked untraceable by associate")


def _tier_label(match_tier, close_reason: str, match_method: str) -> str:
    """Tier 1 / Tier 2 / Untraceable — the same three buckets the daily digest
    reports, derived the same way, so the two can never disagree."""
    if _declared_untraceable(close_reason, match_method):
        return "Untraceable"
    if match_tier == 1:
        return "Tier 1"
    if match_tier == 2:
        return "Tier 2"
    return "Untraceable"


def _norm_owner(picked_up_by: str) -> str:
    name = (picked_up_by or "").strip()
    return UNASSIGNED if (not name or name.lower() in TEST_OWNERS) else name


def _hours_between(start, end):
    if not start or not end:
        return None
    return round((end - start).total_seconds() / 3600, 2)


def project(review, draft) -> dict:
    """One review+draft -> the analytics record, built on the export's `row_for`
    and extended with the fields only reporting needs."""
    from server.services.sheet_export import row_for
    row = row_for(review, draft)

    received = row.get("received_at")
    finished = row.get("sent_at") or row.get("closed_at")
    return {
        "review_id":         row.get("review_id") or "",
        "date":              received.strftime("%Y-%m-%d") if received else None,
        "rating":            row.get("rating"),
        "language":          row.get("language") or None,
        "status":            row.get("status") or None,
        "experience":        row.get("experience") or None,
        "vendor":            row.get("vendor") or None,
        "tgid":              row.get("tgid") or None,
        "fulfilment_type":   row.get("fulfilment_type") or None,
        "booking_status":    row.get("booking_status") or None,
        "tier":              _tier_label(row.get("match_tier"),
                                         row.get("close_reason") or "",
                                         row.get("match_method") or ""),
        "traceable":         "Traceable" if row.get("booking_id") else "No booking found",
        "match_method":      row.get("match_method") or "none",
        "l1":                row.get("l1") or None,
        "l2":                row.get("l2") or None,
        "sub_themes":        list(row.get("sub_themes") or []),
        "scenarios":         list(row.get("scenarios") or []),
        "overlay_scenarios": list(row.get("overlay_scenarios") or []),
        "claim_accuracy":    list(row.get("claim_accuracy") or []),
        "resolution_given":  "Yes" if (row.get("resolution") or "").strip() else "No",
        "takedown":          row.get("takedown") or None,
        "outcome_category":  row.get("outcome_category") or None,
        "dss_followed":      row.get("dss_followed") or None,
        "owner":             _norm_owner(row.get("picked_up_by") or ""),
        "sent_route":        row.get("sent_route") or None,
        "close_reason":      (row.get("close_reason") or "")[:40] or None,
        "prompt_version":    row.get("rca_prompt_version") or None,
        # measure inputs
        "solved":            (row.get("status") == "sent"),
        "posted":            bool(row.get("rca_posted_at")),
        "issue_count":       int(row.get("issue_count") or 0),
        "zd_count":          len(row.get("zendesk_tickets") or []),
        "flag_count":        len(row.get("flags") or []),
        "tts_hours":         _hours_between(received, finished),
    }


def values_of(record: dict, key: str) -> list[str]:
    """The record's value(s) for a dimension, as a list. Multi-valued fields are
    unnested; a record with no value returns [] — the caller decides how to show
    that, and `run_query` counts it rather than dropping it."""
    v = record.get(key)
    if v is None or v == "" or v == []:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [str(v)]


# ── query ───────────────────────────────────────────────────────────────────

def matches_filters(record: dict, filters: dict) -> bool:
    """A filter is field -> exact value. A blank/None value means "any"."""
    for key, want in (filters or {}).items():
        if want in (None, ""):
            continue
        if str(want) not in values_of(record, key):
            return False
    return True


def run_query(records: list[dict], dimensions: list[str], measures: list[str],
              filters: dict | None = None, limit: int = 500,
              sort: str | None = None, descending: bool = True) -> dict:
    """Group `records` by `dimensions` and aggregate `measures`.

    `totals` is computed over ALL matching records, never by summing the rows — a
    median or a percentage does not sum. `unset` reports, per dimension, how many
    matching reviews carry no value for it, so "nobody recorded this" cannot be
    read as "the count is zero"."""
    bad_dims = [d for d in dimensions if d not in DIM_BY_KEY]
    bad_meas = [m for m in measures if m not in MEASURES]
    if bad_dims:
        raise ValueError("unknown dimension(s): " + ", ".join(bad_dims))
    if bad_meas:
        raise ValueError("unknown measure(s): " + ", ".join(bad_meas))

    matched = [r for r in records if matches_filters(r, filters or {})]
    unset = {d: sum(1 for r in matched if not values_of(r, d)) for d in dimensions}

    if not dimensions:
        groups = {(): matched} if matched else {}
    else:
        groups = {}
        for rec in matched:
            combos = [()]
            for d in dimensions:
                vals = values_of(rec, d) or [UNSET]
                combos = [c + (v,) for c in combos for v in vals]
            for c in combos:
                groups.setdefault(c, []).append(rec)

    rows = [{"key": list(key), "values": {m: MEASURES[m][1](rs) for m in measures}}
            for key, rs in groups.items()]

    sort_key = sort if sort in MEASURES else (measures[0] if measures else None)
    if sort_key:
        rows.sort(key=lambda r: (r["values"][sort_key] is None,
                                 r["values"][sort_key] if r["values"][sort_key] is not None else 0),
                  reverse=descending)
    else:
        rows.sort(key=lambda r: r["key"])

    return {
        "columns": ([{"key": d, "label": DIM_BY_KEY[d].label, "kind": "dimension"} for d in dimensions]
                    + [{"key": m, "label": MEASURES[m][0], "kind": "measure"} for m in measures]),
        "rows": rows[:limit],
        "row_count": len(rows),
        "truncated": len(rows) > limit,
        "matched": len(matched),
        "scanned": len(records),
        "totals": {m: MEASURES[m][1](matched) for m in measures},
        "unset": unset,
        "unnested": sorted(d for d in dimensions if DIM_BY_KEY[d].multi),
    }


# ── DB-facing glue ──────────────────────────────────────────────────────────

def records(db, start: datetime | None = None, end: datetime | None = None) -> list[dict]:
    """Every review received in [start, end) as an analytics record. Bounds are
    NAIVE UTC to match the stored columns — comparing a naive column against a
    tz-aware parameter makes Postgres reinterpret it through the session timezone
    and silently shift the window (see daily_report._db_bounds)."""
    from server.db import Review, RcaDraft
    q = (db.query(Review, RcaDraft)
           .outerjoin(RcaDraft, RcaDraft.review_id == Review.id)
           # Production data only. Test-owner reviews are excluded here so no
           # downstream measure — grouped or ungrouped — can count them.
           .filter(not_test_row_clause(Review)))
    if start is not None:
        q = q.filter(Review.received_at.isnot(None)).filter(Review.received_at >= start)
    if end is not None:
        q = q.filter(Review.received_at.isnot(None)).filter(Review.received_at < end)
    return [project(r, d) for r, d in q.all()]


def field_registry() -> dict:
    """What the client renders its field picker from, so the two cannot drift."""
    return {
        "views": VIEWS,
        "dimensions": [{"key": d.key, "view": d.view, "label": d.label, "multi": d.multi}
                       for d in DIMENSIONS],
        "measures": [{"key": k, "label": v[0]} for k, v in MEASURES.items()],
    }
