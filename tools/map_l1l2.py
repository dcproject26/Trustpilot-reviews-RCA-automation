#!/usr/bin/env python3
"""
Dump the tag vocabularies our L1/L2 framework has to be mapped onto.

    python3 tools/map_l1l2.py

Run on Replit. Reads three sources and writes nothing.

Matching our L2 NAMES against Headout's review-classifier strings does not
work - they are two vocabularies that were never reconciled, so name similarity
finds coincidences rather than counterparts. The tags are the bridge. That is
what SUPPORT_TAG_MAP already is for the five L2s specified in the VectorShift
brief: an L1/L2 mapped to the support tags that mean the same thing.

Twenty of our thirty-two L1/L2 combinations have no such mapping, so their
similar-support count is zero by construction rather than by measurement.

What this prints:

  1. fct_support_queries.query_tag - every live value with a row count,
     GROUPED BY its hierarchy. The values are paths ("Ticket Redemption
     Details  Meeting Point Related  Meeting Point Is Incorrect/missing"), and
     grouped by their first segment the structure is legible enough to map
     against; flat and alphabetical it is not.
  2. fct_zendesk_tickets - whatever tag-like columns it has, discovered rather
     than assumed, with their live values.
  3. fct_reviews.l2_issues - the review vocabulary, for completeness.

Plus current coverage: which of our L1/L2 have a support mapping and which do
not.

Nothing is written and nothing is guessed. The output is what a mapping gets
written FROM.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import insights as I           # noqa: E402
from server.services import bq_connector as BQ      # noqa: E402
from server.taxonomy import L2_OPTIONS, support_tags_for   # noqa: E402

DAYS = 180
_ZENDESK = "headout-analytics.analytics_reporting.fct_zendesk_tickets"


def _rows(sql):
    try:
        return BQ.run_query(sql)
    except Exception as e:
        print(f"    (query failed: {str(e)[:160]})")
        return []


def support_tags():
    return _rows(f"""
SELECT query_tag AS v, COUNT(*) AS n
FROM `{I._SUPPORT_TABLE}`
WHERE DATE(query_created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)
  AND query_tag IS NOT NULL
GROUP BY v ORDER BY n DESC
""")


def review_l2s():
    cols = BQ.column_types(I._REVIEWS_TABLE)
    dc = next((c for c in ("reviewed_at", "review_created_at", "created_at")
               if c in cols), None)
    where = (f"WHERE DATE(r.{dc}) >= DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)"
             if dc else "")
    return _rows(f"""
SELECT l2v AS v, COUNT(*) AS n
FROM `{I._REVIEWS_TABLE}` r
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
{where}
GROUP BY v ORDER BY n DESC
""")


def zendesk_tag_columns():
    """Find tag-like columns rather than assuming a name."""
    cols = BQ.column_types(_ZENDESK)
    if not cols:
        return None, []
    wanted = [c for c, t in cols.items()
              if any(k in c.lower() for k in
                     ("tag", "category", "reason", "type", "topic", "issue",
                      "subject", "l1", "l2"))
              and "STRING" in str(t).upper()]
    return cols, wanted


def print_hierarchy(rows, title):
    """
    Group path-shaped tags by their first segment.

    Segments are separated by a double space in this vocabulary. Grouped, the
    structure is legible enough to map an L2 against; flat and alphabetical it
    is not.
    """
    print("\n" + "=" * 78)
    print(f"{title}  ({len(rows)} distinct, {sum(r['n'] for r in rows):,} rows)")
    print("=" * 78)
    groups = defaultdict(list)
    for r in rows:
        head = str(r["v"]).split("  ")[0].strip() or "(blank)"
        groups[head].append((r["n"], r["v"]))
    for head in sorted(groups, key=lambda h: -sum(n for n, _ in groups[h])):
        items = sorted(groups[head], reverse=True)
        print(f"\n  {head}   [{sum(n for n, _ in items):,} rows]")
        for n, v in items:
            tail = str(v)[len(head):].strip() or "(no sub-path)"
            print(f"      {n:>7,}  {tail}")


def main():
    if not BQ.available():
        print("No BigQuery connection on this machine.\n"
              "Run this on Replit, where the connector is bound.")
        return 2

    # --- coverage ---------------------------------------------------------
    ours = [(l1, l2) for l1, l2s in L2_OPTIONS.items() for l2 in l2s]
    missing = [(l1, l2) for l1, l2 in ours if not support_tags_for(l1, l2)]
    print("=" * 78)
    print(f"SUPPORT TAG COVERAGE - {len(ours) - len(missing)} of {len(ours)} "
          f"L1/L2 mapped, {len(missing)} unmapped")
    print("=" * 78)
    print("\nUnmapped - similar support is zero by construction for these:\n")
    for l1, l2 in missing:
        print(f"   {l1:<22} {l2}")

    print_hierarchy(support_tags(), "fct_support_queries.query_tag")

    # --- zendesk ----------------------------------------------------------
    print("\n" + "=" * 78)
    print("fct_zendesk_tickets")
    print("=" * 78)
    cols, tagcols = zendesk_tag_columns()
    if cols is None:
        print("  table not readable from here")
    else:
        print(f"  {len(cols)} columns; tag-like: {tagcols or 'none found'}")
        for c in tagcols[:6]:
            rows = _rows(f"""
SELECT {c} AS v, COUNT(*) AS n
FROM `{_ZENDESK}`
WHERE {c} IS NOT NULL
GROUP BY v ORDER BY n DESC LIMIT 60
""")
            if rows:
                print(f"\n  -- {c} ({len(rows)} shown) --")
                for r in rows:
                    print(f"      {r['n']:>7,}  {r['v']}")

    # --- reviews ----------------------------------------------------------
    rl2 = review_l2s()
    print("\n" + "=" * 78)
    print(f"fct_reviews.l2_issues  ({len(rl2)} distinct)")
    print("=" * 78)
    for r in rl2:
        print(f"  {r['n']:>7,}  {r['v']}")

    print("\n" + "=" * 78)
    print("Paste this back. The support-tag hierarchy is what the twenty "
          "unmapped\nL1/L2 get mapped onto, the same way the five from the "
          "VS brief already are.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
