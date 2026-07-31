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


def selftest():
    """Does the support->booking join actually join?

    A dry run type-checks the join but never executes it, and the join uses
    SAFE_CAST, which returns NULL rather than raising on anything that is not
    a plain number. So if fct_support_queries.booking_id carries a prefix, a
    padded id, or an id from a different space, every row silently drops and
    the search returns nothing forever - indistinguishable from "this guest
    never contacted support". This runs the join over a small recent slice and
    reports what survives.
    """
    from server.config import (is_live, BIGQUERY_SUPPORT_TABLE,
                               BIGQUERY_BOOKINGS_TABLE)
    if not is_live("bigquery"):
        print("MOCK_MODE — run this where BigQuery credentials are set.")
        return 2
    from server.services.bigquery import _run_query

    sql = f"""
    WITH recent AS (
      SELECT booking_id
      FROM `{BIGQUERY_SUPPORT_TABLE}`
      WHERE query_created_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 30 DAY)
        AND booking_id IS NOT NULL
      LIMIT 200000
    )
    SELECT
      COUNT(*)                                                   AS contacts,
      COUNTIF(SAFE_CAST(r.booking_id AS INT64) IS NULL)           AS not_a_number,
      COUNTIF(b.booking_id IS NOT NULL)                           AS joined_to_a_booking,
      ANY_VALUE(r.booking_id)                                     AS sample_id
    FROM recent r
    LEFT JOIN `{BIGQUERY_BOOKINGS_TABLE}` b
           ON SAFE_CAST(r.booking_id AS INT64) = b.booking_id
    """
    try:
        rows = _run_query(sql, [])
    except Exception as e:
        print(f"[FAIL] the join check could not run: {e}")
        return 1
    if not rows:
        print("[FAIL] no support contacts at all in the last 30 days — check "
              "the table name.")
        return 1

    r = rows[0]
    total  = int(getattr(r, "contacts", 0) or 0)
    bad    = int(getattr(r, "not_a_number", 0) or 0)
    joined = int(getattr(r, "joined_to_a_booking", 0) or 0)
    sample = getattr(r, "sample_id", "")

    print(f"support contacts in the last 30 days   {total:,}")
    print(f"  booking_id that is not a number      {bad:,}")
    print(f"  rows that joined to a real booking   {joined:,}")
    print(f"  a sample booking_id as stored        {sample!r}\n")

    if total and joined == 0:
        print("[FAIL] the join matches NOTHING. The search would return no "
              "booking, ever, and would look like an honest 'no match'.")
        print("       Compare the sample id above with fct_bookings.booking_id "
              "— a prefix, padding or a different id space would do this.")
        return 1
    if total and joined / total < 0.5:
        print(f"[warn] only {joined / total:.0%} of contacts join to a booking. "
              f"Worth understanding why before relying on this path.")
        return 0
    print(f"[ ok ] the join works — {joined / total:.0%} of recent support "
          f"contacts resolve to a booking.")
    return 0


def _reviews_with_indicators(db, author: str = ""):
    """Reviews whose matching indicators were extracted and stored, newest first."""
    from server.db import RcaDraft, Review
    q = db.query(Review)
    if author:
        q = q.filter(Review.author.ilike(f"%{author}%"))
    out = []
    for rv in q.order_by(Review.received_at.desc()).limit(200).all():
        d = db.query(RcaDraft).filter(RcaDraft.review_id == rv.id).first()
        ind = ((d.extracted_signals or {}).get("match_indicators") or {}) if d else {}
        if ind:
            out.append((rv, ind))
    return out


def list_reviews():
    from server.db import SessionLocal, init_db
    init_db()
    db = SessionLocal()
    try:
        rows = _reviews_with_indicators(db)
        if not rows:
            print("No review has stored indicators yet. They are written when a "
                  "review goes through matching without a booking id.")
            return 1
        print(f"{len(rows)} review(s) with extracted indicators, newest first:\n")
        for rv, ind in rows[:40]:
            dates = ",".join(ind.get("dates_mentioned") or []) or "—"
            print(f"  {rv.id:<22} {(rv.author or '—')[:20]:<20} "
                  f"dates {dates:<24} {(rv.body_english or '')[:44]}")
        print(f"\nRun one:  python3 tools/check_support_search.py "
              f"--review {rows[0][0].id} --run")
        return 0
    finally:
        db.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="Sven", help="guest name to search for")
    ap.add_argument("--dates", default="", help="comma-separated YYYY-MM-DD")
    ap.add_argument("--venue", default="", help="experience name fragment")
    ap.add_argument("--run", action="store_true",
                    help="execute the query instead of only validating it")
    ap.add_argument("--selftest", action="store_true",
                    help="prove the support->booking join actually joins")
    ap.add_argument("--review", default="",
                    help="use a real review's extracted indicators, by review id")
    ap.add_argument("--author", default="",
                    help="same, but find the review by author name (newest wins)")
    ap.add_argument("--list", action="store_true",
                    help="list reviews that have extracted indicators, and stop")
    args = ap.parse_args()

    if args.list:
        return list_reviews()

    if args.selftest:
        return selftest()

    ind = dict(SAMPLE)
    name = args.name

    # A real review beats the sample: SAMPLE's dates are a reconstruction of
    # Sven's review, and testing a search against remembered facts proves
    # nothing about the facts extraction actually produced.
    if args.review or args.author:
        from server.db import SessionLocal, init_db, RcaDraft, Review
        init_db()
        db = SessionLocal()
        try:
            if args.author:
                found = _reviews_with_indicators(db, args.author)
                if not found:
                    print(f"no review by an author matching {args.author!r} has "
                          f"stored indicators. Try:  python3 "
                          f"tools/check_support_search.py --list")
                    return 1
                rv, real = found[0]
                if len(found) > 1:
                    print(f"{len(found)} reviews match {args.author!r}; using the "
                          f"newest ({rv.id}). The others:")
                    for other, _ in found[1:6]:
                        print(f"    {other.id}  {other.received_at}")
                    print()
            else:
                d = db.query(RcaDraft).filter(RcaDraft.review_id == args.review).first()
                rv = db.query(Review).filter(Review.id == args.review).first()
                if not d or not rv:
                    print(f"no review/draft with id {args.review}. Try:  python3 "
                          f"tools/check_support_search.py --list")
                    return 1
                real = (d.extracted_signals or {}).get("match_indicators") or {}
                if not real:
                    print(f"review {args.review} has no extracted indicators stored "
                          f"— it was matched by booking id, or predates extraction.")
                    return 1
            ind = dict(real)
            name = (rv.author or "") or real.get("guest_name") or ""
            print(f"review {rv.id} — author {name!r}")
            print(f"  {(rv.body_english or rv.body_original or '')[:200]}")
            print(f"  indicators as extracted: {real}\n")
        finally:
            db.close()

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

    sql, params = support_search_sql(ind, author=name)
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
        rows = asyncio.run(find_via_support(ind, author=name))
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
