#!/usr/bin/env python3
"""
Does the window picker actually reach the average-rating queries?

    python3 tools/test_rating_window.py

No BigQuery, no server, no data. It calls get_insights with the query
executor swapped for a recorder, then reads the SQL that was built.

This exists because the end-to-end test cannot answer the question anywhere
BigQuery is not bound: with no warehouse every rating comes back null, the
sample-size comparison is skipped, and the run passes without having tested
anything. The SQL is the thing under test, so test the SQL.

The failure it guards against is specific and silent. The rating queries sit
in the same builder as every other metric; dropping the window clause from
them - as a fixed-lookback experiment did - leaves the picker working
everywhere else on the panel, so the page looks fine while two tiles report a
different period from the heading above them.

Exit code is 0 only when every check passes.
"""
import asyncio
import re
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "ok  ", "FAIL"
_failures = []


def check(name, ok, detail="", hint=""):
    """detail is shown either way; hint only when the check fails.

    Printing a failure explanation next to a passing check reads as a
    contradiction - "ok ... all three windows built the same SQL" says the
    opposite of what happened.
    """
    line = f"  {PASS if ok else FAIL}  {name}"
    if detail:
        line += f"   {detail}"
    if hint and not ok:
        line += f"   {hint}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def capture(window_days, anchor="2026-07-22"):
    """Run get_insights with a recording executor and return the SQL it built."""
    from server.services import insights as I

    seen = []

    async def _rec(sql, params):
        seen.append(sql)
        return []

    # get_insights returns zeros before building any SQL when BigQuery is not
    # connected, so the connection check has to be satisfied for the builder to
    # run at all. Nothing is executed - _rec never touches a warehouse.
    orig_run, orig_live, orig_mock = I._run, I.is_live, I.MOCK_MODE
    I._run, I.is_live, I.MOCK_MODE = _rec, (lambda _: True), False
    try:
        asyncio.run(I.get_insights(
            {"id": "1", "tid": "T1", "vid": "V1", "tgid": "G1",
             "date_of_visit": anchor},
            l1="Experience Issues", l2="Meeting Point Issues",
            window=f"{window_days}d"))
    finally:
        I._run, I.is_live, I.MOCK_MODE = orig_run, orig_live, orig_mock
    return seen


def rating_queries(sqls):
    """The two average-rating queries, picked out by what they select."""
    return [s for s in sqls if "avg_rating" in s and "n_ratings" in s]


def main():
    print("building the queries for three windows (nothing is executed)\n")
    try:
        by_window = {w: capture(w) for w in (7, 30, 90)}
    except Exception as e:
        print(f"  {FAIL}  get_insights raised: {type(e).__name__}: {e}")
        return 1

    for w in (7, 30, 90):
        rq = rating_queries(by_window[w])
        print(f"window {w}d - {len(by_window[w])} queries built, "
              f"{len(rq)} of them average-rating")
        check(f"{w}d: both rating queries built", len(rq) == 2, f"got {len(rq)}")
        if len(rq) != 2:
            continue

        # Scope: one keyed on the tour+vendor pair, one on the experience.
        check(f"{w}d: one is TID+VID scoped",
              any("tour_id = @tid" in s and "vendor_id = @vid" in s for s in rq))
        check(f"{w}d: one is TGID scoped",
              any("experience_id = @tgid" in s for s in rq))

        for i, s in enumerate(rq):
            # The window clause itself, with the interval the picker asked for.
            ok_int = f"INTERVAL {w} DAY" in s
            check(f"{w}d: rating query {i} carries INTERVAL {w} DAY", ok_int,
                  hint="the window is not reaching this query - it would "
                       "report a different period from the heading above it")
            check(f"{w}d: rating query {i} anchors on the visit date",
                  "@anchor" in s and "experience_date" in s)
            # Averaged over ALL ratings, not the negative ones. An average
            # taken over reviews already filtered to <= 3 stars could never
            # exceed 3 and would say nothing about the experience.
            check(f"{w}d: rating query {i} is not filtered to negatives",
                  "rating <= " not in s,
                  hint="averaging over negative reviews only caps the tile at 3")
            check(f"{w}d: rating query {i} counts a booking once",
                  "GROUP BY r.booking_id" in s)
            check(f"{w}d: rating query {i} is CUSTOMER-sourced",
                  "r.source = 'CUSTOMER'" in s)
        print()

    # The intervals must actually differ between windows. Identical SQL across
    # three windows is exactly what a dropped window clause looks like, and it
    # is invisible on a dashboard that has no warehouse to disagree with it.
    print("the window changes the SQL")
    ints = {}
    for w in (7, 30, 90):
        rq = rating_queries(by_window[w])
        ints[w] = sorted({m for s in rq for m in re.findall(r"INTERVAL (\d+) DAY", s)})
    check("each window builds its own interval",
          ints[7] == ["7"] and ints[30] == ["30"] and ints[90] == ["90"],
          f"7d={ints[7]} 30d={ints[30]} 90d={ints[90]}")
    check("the three are not identical",
          len({str(rating_queries(by_window[w])) for w in (7, 30, 90)}) == 3,
          hint="all three windows built the same rating SQL")

    print("\n" + "-" * 62)
    sample = rating_queries(by_window[30])
    if sample:
        print("30d TGID rating query as built:\n")
        tgid = next((s for s in sample if "@tgid" in s), sample[0])
        for line in tgid.strip().splitlines():
            print("   " + line)
    print("-" * 62)
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
