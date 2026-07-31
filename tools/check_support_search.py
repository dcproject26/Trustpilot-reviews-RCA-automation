#!/usr/bin/env python3
"""
Validate the support-anchored booking search against the live BigQuery schema.

    python3 tools/check_support_search.py           # dry-run the query
    python3 tools/check_support_search.py --run     # actually run it and show rows

Why this exists: the query in find_via_support() joins fct_support_queries to
fct_bookings and reads query_tag / query_type / contact_type /
query_created_at. Those columns were read off existing queries in this repo,
not off the live schema, and a column that does not exist fails at match time
on a real review rather than here. A BigQuery dry run type-checks every table,
column and operand without scanning a byte, so this is the cheap way to find
out before a guest's review does.

It builds the query through server.services.bigquery.support_search_sql, the
same function the service calls, so it cannot drift from what actually runs.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Sven's review, the case this path was built for: a common first name, two
# dates, no venue worth searching.
SAMPLE = {
    "guest_name": "Sven",
    "experience_or_venue": None,
    "city_or_country": None,
    "dates_mentioned": ["2026-10-20", "2026-06-20"],
    "issue_terms": ["falsches Datum", "wrong date"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Sven", help="guest name to search for")
    ap.add_argument("--dates", default="", help="comma-separated YYYY-MM-DD")
    ap.add_argument("--venue", default="", help="experience name fragment")
    ap.add_argument("--run", action="store_true",
                    help="execute the query instead of only validating it")
    args = ap.parse_args()

    ind = dict(SAMPLE)
    if args.dates:
        ind["dates_mentioned"] = [d.strip() for d in args.dates.split(",") if d.strip()]
    if args.venue:
        ind["experience_or_venue"] = args.venue

    from server.config import (is_live, BIGQUERY_SUPPORT_TABLE,
                               BIGQUERY_BOOKINGS_TABLE)
    from server.services.bigquery import support_search_sql

    print(f"support table  {BIGQUERY_SUPPORT_TABLE}")
    print(f"bookings table {BIGQUERY_BOOKINGS_TABLE}")
    print(f"bigquery live  {is_live('bigquery')}\n")

    sql, params = support_search_sql(ind, author=args.name)
    if sql is None:
        print("Nothing to anchor on (no name and no dates) — the search would "
              "correctly decline to run. Pass --name or --dates.")
        return 0

    as_dict = {}
    for p in params:
        as_dict[p.name] = ("STRING", getattr(p, "values", None)
                           if hasattr(p, "values") else p.value)
    print("parameters:")
    for k, (_t, v) in as_dict.items():
        print(f"  @{k} = {v}")
    print("\n" + "─" * 74)
    print(sql.strip())
    print("─" * 74 + "\n")

    if not is_live("bigquery"):
        print("MOCK_MODE — no BigQuery connection, so the schema cannot be "
              "checked from here. Run this where BigQuery credentials are set.")
        return 2

    try:
        from server.services.bq_connector import dry_run
    except Exception as e:
        print(f"could not load the BigQuery connector: {e}")
        return 2

    res = dry_run(sql, as_dict)
    if res.get("ok"):
        gb = (res.get("bytes") or 0) / 1e9
        print(f"[ ok ] the query type-checks against the live schema "
              f"({gb:.2f} GB would be scanned)")
    else:
        print("[FAIL] BigQuery rejected the query:\n")
        print(f"       {res.get('error')}\n")
        print("       Usually a column name. Compare the SELECT list against")
        print("       the real fct_support_queries schema and fix")
        print("       support_search_sql() in server/services/bigquery.py.")
        return 1

    if args.run:
        import asyncio
        from server.services.bigquery import find_via_support
        rows = asyncio.run(find_via_support(ind, author=args.name))
        print(f"\n{len(rows)} booking(s):\n")
        for r in rows:
            print(f"  {r['id']:>12}  {r.get('visitDate',''):>10}  "
                  f"{(r.get('guestName') or '')[:24]:24}  "
                  f"{r.get('contact_count', 0)}x  {r.get('contact_tags','')[:40]}")
            print(f"                {(r.get('experienceName') or '')[:60]}")
        if not rows:
            print("  (none — either no such guest contacted support, or the "
                  "name/dates need widening)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
