#!/usr/bin/env python3
"""
When a query breaks, or a mapping is missing, does the panel say so?

    python3 tools/test_insights_degrade.py

No BigQuery, no server. get_insights runs with the executor replaced by one
that answers, or raises, per query.

Two failures this covers, both of which produce a confident wrong number
rather than an error:

  1. A BigQuery error used to be swallowed into []. Downstream that is
     indistinguishable from an honest zero, and the dashboard states honest
     zeros affirmatively - "no negative reviews", "no bookings". So an outage
     or a bad column name rendered as a claim about the vendor.

  2. The similar-REVIEWS query was gated on the SUPPORT-tag map. 11 of 32
     L1/L2 pairs are deliberately unmapped for support, and every one of them
     forced its reviews tile to "0 of 47 - 0.0%" while l2_variants held live
     aliases worth thousands of rows.

Exit code is 0 only when every check passes.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS, FAIL = "ok  ", "FAIL"
_failures = []


def check(name, ok, detail="", hint=""):
    line = f"  {PASS if ok else FAIL}  {name}"
    if detail:
        line += f"   {detail}"
    if hint and not ok:
        line += f"   {hint}"
    print(line)
    if not ok:
        _failures.append(name)
    return ok


def run(l1, l2, break_on=(), rows=None):
    """
    Run get_insights against a fake warehouse.

    break_on: substrings; a query containing one raises instead of answering.
    rows:     what a working query returns (one row, count 7 by default).
    """
    from server.services import insights as I
    from server.services import bq_connector as BQ

    default = rows if rows is not None else [{"c": 7, "avg_rating": 4.2,
                                              "n_ratings": 11, "completed": 5,
                                              "completed_by_booking_status": 5,
                                              "unfulfilled": 2, "total": 7}]

    # Patch the CONNECTOR, not insights._run. Stubbing _run skips the very
    # code under test: _run is what turns a raised exception into a result,
    # and replacing it meant reverting that fix still passed this file. The
    # fake raises exactly where BigQuery would.
    async def _fake_query(sql, params=None, *a, **kw):
        for frag in break_on:
            if frag in sql:
                raise RuntimeError(f"simulated BigQuery failure on {frag!r}")
        return list(default)

    orig = (BQ.run_query_async, I.is_live, I.MOCK_MODE)
    BQ.run_query_async, I.is_live, I.MOCK_MODE = (
        _fake_query, (lambda _: True), False)
    try:
        return asyncio.run(I.get_insights(
            {"id": "1", "tid": "T1", "vid": "V1", "tgid": "G1",
             "date_of_visit": "2026-07-22"}, l1=l1, l2=l2, window="30d"))
    finally:
        BQ.run_query_async, I.is_live, I.MOCK_MODE = orig


def main():
    from server.services.insights import l2_variants
    from server.taxonomy import support_tags_for

    # --- 1. a broken query is reported as broken ----------------------------
    print("a failing query is named, not zeroed\n")
    # fct_reviews appears in the review queries and the rating queries.
    out = run("Experience Issues", "Meeting Point Issues",
              break_on=("fct_reviews",))
    failed = out.get("_failed_queries") or []
    check("_failed_queries is populated", bool(failed), f"{failed}")
    check("the review queries are named",
          "reviews_total" in failed,
          f"{failed}",
          hint="a failed query is indistinguishable from an honest zero")
    check("_failed_detail carries the reason",
          "simulated" in str(out.get("_failed_detail") or {}),
          hint="no reason recorded - the log is the only trace")
    # The queries that did NOT break must still be there.
    check("unbroken queries still answered",
          (out.get("total_support_queries_30d") or 0) > 0,
          f"support total={out.get('total_support_queries_30d')}",
          hint="one broken query should not zero the rest")

    print("\nnothing broken -> nothing reported\n")
    clean = run("Experience Issues", "Meeting Point Issues")
    check("_failed_queries empty on a clean run",
          clean.get("_failed_queries") == [], f"{clean.get('_failed_queries')}")

    # --- 2. the two gates are independent -----------------------------------
    # Find a real L1/L2 that has L2 variants but NO support-tag mapping. That
    # is the exact shape the shared gate broke, and it is drawn from the live
    # taxonomy rather than invented, so this stops being a test the day the
    # mapping is filled in.
    print("\nan unmapped support tag does not silence the reviews query\n")
    # Real pairs: every (l1, l2) the support map knows about, plus every L2
    # bucket paired with the L1s in that map. support_tags_for is the
    # authority on which are unmapped.
    from server.taxonomy import SUPPORT_TAG_MAP
    from server.services.insights import _L2_BUCKETS
    l1s = sorted({k[0] for k in SUPPORT_TAG_MAP if k[0] != "Supply Partner Issue"})
    candidates = [(a, b) for a in l1s for b in sorted(_L2_BUCKETS)]
    pair = next(((a, b) for a, b in candidates
                 if support_tags_for(a, b) is None and l2_variants(b)), None)

    if not pair:
        print("  ..    every L1/L2 pair now has a support-tag mapping - the\n"
              "        shared-gate bug cannot be reproduced from live data.\n"
              "        Using a synthetic pair instead.")
        pair = ("Experience Issues", "Meeting Point Issues")
        out2 = run(*pair)
        check("reviews compared when support is unmapped",
              (out2.get("similar_reviews_30d") or 0) > 0,
              f"similar_reviews={out2.get('similar_reviews_30d')}")
    else:
        l1, l2 = pair
        print(f"  using {l1} / {l2} - no support-tag mapping, "
              f"{len(l2_variants(l2))} L2 variants")
        out2 = run(l1, l2)
        check("similar reviews still counted",
              (out2.get("similar_reviews_30d") or 0) > 0,
              f"similar_reviews={out2.get('similar_reviews_30d')}",
              hint="the reviews query is still gated on the support-tag map")
        check("support contacts correctly not counted",
              (out2.get("similar_support_queries_30d") or 0) == 0,
              f"similar_support={out2.get('similar_support_queries_30d')}")
        why = out2.get("_partial_because") or ""
        check("_partial_because explains which half was skipped",
              "support" in why.lower(), f"{why!r}",
              hint="a zero support count with no explanation reads as a fact")
        check("_partial_because does not blame the reviews half",
              "reviews not compared" not in why, f"{why!r}")

    print("-" * 62)
    if _failures:
        print(f"{len(_failures)} FAILED: {', '.join(_failures)}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
