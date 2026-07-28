#!/usr/bin/env python3
"""
Validate every Experience Insights query against the live BigQuery schema.

    python3 tools/dryrun_insights.py

Run it on Replit, where the BigQuery connector is bound. It scans no data and
costs nothing: a dry run type-checks the query in full - every table, every
column, every operand type - and returns without executing.

This exists because insights.py was written from Looker views pasted into a
chat, and a pasted view tells you what a dimension is CALLED, not what the
underlying column is named or what type it holds. Two assumptions in particular
have never been checked:

    fct_reviews.source              assumed to exist   (avg_rating filters it)
    fct_support_queries.tags        assumed to be a STRING, because the LookML
                                    compares it to "CHATBOT, CHATBOT-TRANSFER"

Either being wrong breaks a query, and insights.py swallows query errors by
design - _run() catches everything and returns [], so the tile reads 0 rather
than raising. A broken query and a genuinely quiet experience look identical on
the dashboard. This is the check that tells them apart.

Exit code is 0 only when every query validates.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import insights as I           # noqa: E402
from server.services import bq_connector as BQ      # noqa: E402

# A booking is needed only to give the queries a shape to build around. The
# values never reach the warehouse - a dry run does not execute - so any
# well-formed ids will do.
SAMPLE = {"tid": "1", "vid": "1", "tgid": "1", "visitDate": "2026-06-01"}

# L1/L2 chosen to exercise both support-tag paths: an exact IN list and a
# LIKE-pattern list. Running only one would leave the other untested, and they
# generate different SQL.
CASES = [
    ("exact tag list",  "Operations Issue", "Meeting Point Issues"),
    ("LIKE patterns",   "Operations Issue",
     "Content - Instructions not clear / Misleading Info"),
    ("no tag mapping",  "Service Issues",   "Long Queues"),
]

# Columns the code assumes into existence. Reported up front, because
# "tags is ARRAY<STRING>" explains a failure that BigQuery describes only as
# "No matching signature for operator IN".
ASSUMED = {
    I._REVIEWS_TABLE: ["source", "rating", "booking_id", "issues"],
    I._SUPPORT_TABLE: ["tags", "query_tag", "booking_id", "is_auto_resolved",
                       "contact_type", "query_type", "query_created_at"],
    I._BOOKINGS_TABLE: ["tour_id", "vendor_id", "experience_id",
                        "experience_date", "booking_status", "booking_id"],
    I._FULFILMENTS_TABLE: ["fulfilment_status", "completion_type",
                           "vendor_id", "booking_id"],
}

# Columns Looker defines but the warehouse does not materialise. insights.py
# rebuilds these in SQL, so absent is the expected answer and reporting them as
# missing buries a real failure in noise.
DERIVED = {"query_category"}


def capture(l1, l2):
    """Every (sql, params) insights.py would send for this issue."""
    seen = []

    async def fake_run(sql, params):
        seen.append((sql, params))
        return []

    real_run, real_mock, real_live = I._run, I.MOCK_MODE, I.is_live
    I._run, I.MOCK_MODE, I.is_live = fake_run, False, (lambda *a, **k: True)
    try:
        asyncio.run(I.get_insights(dict(SAMPLE), l1, l2, window="30d"))
    finally:
        I._run, I.MOCK_MODE, I.is_live = real_run, real_mock, real_live
    return seen


def main():
    if not BQ.available():
        print("No BigQuery connection on this machine.\n"
              "Run this on Replit, where the connector is bound.")
        return 2

    print("=" * 72)
    print("Columns the code assumes exist")
    print("=" * 72)
    missing = []
    for table, cols in ASSUMED.items():
        got = BQ.column_types(table, cols)
        print(f"\n{table.split('.')[-1]}")
        for c in cols:
            t = got.get(c)
            if t:
                mark, note = "  ", t
            elif c in DERIVED:
                mark, note = "  ", "derived in SQL - not a column, as expected"
            else:
                mark, note = "!!", "MISSING"
                missing.append(f"{table.split('.')[-1]}.{c}")
            print(f"  {mark} {c:<20} {note}")

    print("\n" + "=" * 72)
    print("Dry run - every query insights.py builds")
    print("=" * 72)

    failures = []
    for label, l1, l2 in CASES:
        queries = capture(l1, l2)
        print(f"\n{label}  ({l1} / {l2})  - {len(queries)} queries")
        for i, (sql, params) in enumerate(queries):
            res = BQ.dry_run(sql, params)
            if res["ok"]:
                gb = res["bytes"] / 1e9
                print(f"  ok    #{i}  would scan {gb:6.2f} GB")
            else:
                err = " ".join(str(res["error"]).split())[:200]
                print(f"  FAIL  #{i}  {err}")
                failures.append((label, i, err, sql))

    print("\n" + "=" * 72)
    if missing:
        print(f"MISSING COLUMNS: {', '.join(sorted(set(missing)))}")
    if failures:
        print(f"{len(failures)} QUERIES FAILED\n")
        # Print the SQL for the first failure only. The rest are usually the
        # same root cause, and dumping every one buries it.
        label, i, err, sql = failures[0]
        print(f"First failure - {label} #{i}:\n{err}\n\n{sql.strip()}")
        return 1
    print("All queries validate against the live schema.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
