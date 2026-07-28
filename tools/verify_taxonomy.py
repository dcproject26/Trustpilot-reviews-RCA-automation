#!/usr/bin/env python3
"""
Does the taxonomy match the warehouse?

    python3 tools/verify_taxonomy.py

Run on Replit. Four queries, roughly a gigabyte, and it answers the question a
dry run cannot: the SQL is valid, but do the STRINGS it matches on exist?

Two mappings decide whether "similar issues" finds anything:

    SUPPORT_TAG_MAP   L1/L2 -> the query_tag values that mean the same issue
    _L2_BUCKETS       an L2 -> every spelling of it in fct_reviews

Both were written by hand from documents. A value that is slightly off - a
double space, a different capitalisation, a renamed tag - matches zero rows and
reports "no similar issues" rather than failing. That is indistinguishable on
the dashboard from an experience with genuinely no history, which is the whole
problem: the number looks fine and means nothing.

This lists what is actually in the warehouse, marks every configured value that
matches nothing, and suggests the closest real value for each dead one.

Exit code is 0 only when every configured value matches at least one row.
"""
import os
import re
import sys
from collections import defaultdict
from difflib import get_close_matches

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import insights as I           # noqa: E402
from server.services import bq_connector as BQ      # noqa: E402
from server.taxonomy import SUPPORT_TAG_MAP         # noqa: E402

DAYS = 180


def norm(s):
    """Collapse whitespace and case. A double space is the likeliest typo."""
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


def live_query_tags():
    rows = BQ.run_query(f"""
SELECT query_tag AS v, COUNT(*) AS n
FROM `{I._SUPPORT_TABLE}`
WHERE DATE(query_created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)
GROUP BY v
""")
    return {r["v"]: r["n"] for r in rows if r["v"]}


def review_date_column():
    """
    Find the date column on fct_reviews instead of assuming one.

    fct_support_queries turned out to use query_created_at rather than
    created_at, so guessing this one would be repeating a mistake the schema
    check already caught once.
    """
    cols = BQ.column_types(I._REVIEWS_TABLE)
    for c in ("reviewed_at", "review_created_at", "created_at", "review_date",
              "submitted_at"):
        if c in cols:
            return c
    dated = [c for c, t in cols.items()
             if t in ("DATE", "TIMESTAMP", "DATETIME")]
    return dated[0] if dated else None


def live_l2_values(date_col):
    where = (f"WHERE DATE(r.{date_col}) >= "
             f"DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)") if date_col else ""
    rows = BQ.run_query(f"""
SELECT l2v AS v, COUNT(*) AS n
FROM `{I._REVIEWS_TABLE}` r
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
{where}
GROUP BY v
""")
    return {r["v"]: r["n"] for r in rows if r["v"]}


def report(title, configured, live, is_pattern=False):
    """
    configured: {label: [values]}   live: {real_value: row_count}

    A configured value is dead when nothing in `live` matches it. Patterns are
    matched as SQL LIKE; plain values by normalised equality, because that is
    the comparison that forgives the typo we are hunting.
    """
    print("\n" + "=" * 74)
    print(title)
    print("=" * 74)

    live_norm = defaultdict(int)
    for v, n in live.items():
        live_norm[norm(v)] += n
    live_keys = list(live_norm)

    dead = []
    for label, values in sorted(configured.items()):
        lines, hit_total = [], 0
        for v in values:
            if is_pattern:
                rx = re.compile("^" + re.escape(norm(v)).replace(r"\%", ".*") + "$")
                n = sum(c for k, c in live_norm.items() if rx.match(k))
            else:
                n = live_norm.get(norm(v), 0)
            hit_total += n
            if n:
                lines.append(f"      {n:>8,}  {v}")
            else:
                near = get_close_matches(norm(v), live_keys, n=1, cutoff=0.75)
                hint = f"   closest live value: {near[0]!r}" if near else \
                       "   nothing similar in the warehouse"
                lines.append(f"      {'DEAD':>8}  {v}{hint}")
                dead.append((label, v))
        mark = "  " if hit_total else "!!"
        print(f"\n{mark} {label}   ->  {hit_total:,} rows")
        for ln in lines:
            print(ln)
    return dead


def main():
    if not BQ.available():
        print("No BigQuery connection on this machine.\n"
              "Run this on Replit, where the connector is bound.")
        return 2

    date_col = review_date_column()
    tags_live = live_query_tags()
    l2_live = live_l2_values(date_col)
    print(f"Warehouse, last {DAYS} days "
          f"(fct_reviews windowed on {date_col or 'NOTHING - no date column found'}): "
          f"{len(tags_live)} distinct query_tag, {len(l2_live)} distinct l2_issue")

    exact = {f"{l1} / {l2}": v for (l1, l2), v in SUPPORT_TAG_MAP.items()
             if isinstance(v, list)}
    likes = {f"{l1} / {l2}": v.get("like_any", [])
             for (l1, l2), v in SUPPORT_TAG_MAP.items() if isinstance(v, dict)}

    dead = report("SUPPORT_TAG_MAP - exact query_tag lists", exact, tags_live)
    dead += report("SUPPORT_TAG_MAP - LIKE patterns", likes, tags_live,
                   is_pattern=True)
    dead += report("_L2_BUCKETS - review issue spellings", I._L2_BUCKETS, l2_live)

    # --- is the NAR exclusion keying on the right thing? --------------------
    print("\n" + "=" * 74)
    print("NAR exclusion")
    print("=" * 74)
    rows = BQ.run_query(f"""
SELECT
  COUNTIF(is_auto_resolved)                                        AS auto_flag,
  COUNTIF(REGEXP_CONTAINS(IFNULL(query_tag,''), r'(?i)Auto resolved')) AS auto_str,
  COUNTIF(is_auto_resolved
          AND NOT REGEXP_CONTAINS(IFNULL(query_tag,''), r'(?i)Auto resolved'))
                                                                   AS flag_only,
  COUNT(*)                                                         AS total
FROM `{I._SUPPORT_TABLE}`
WHERE DATE(query_created_at) >= DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)
""")
    if rows:
        r = rows[0]
        print(f"  is_auto_resolved = TRUE          {r['auto_flag']:>10,}")
        print(f"  query_tag matches 'Auto resolved' {r['auto_str']:>10,}")
        print(f"  flagged but the regex misses it  {r['flag_only']:>10,}"
              "   <- excluded by Looker, kept by us")
        print(f"  total rows                       {r['total']:>10,}")

    # Which live values the NAR regex drops. An obvious guest contact in this
    # list means the pattern is too broad - "NAR" is matched as a substring.
    print("\n  values the NAR regex excludes:")
    hits = [(v, n) for v, n in sorted(tags_live.items(), key=lambda x: -x[1])
            if re.search(I._NAR_PATTERN.replace("(?i)", ""), v, re.I)]
    if not hits:
        print("    NONE - the whole exclusion is dead, and every non-guest "
              "contact is still in the denominator")
    for v, n in hits:
        print(f"    {n:>8,}  {v}")

    print("\n" + "=" * 74)
    if dead:
        print(f"{len(dead)} CONFIGURED VALUES MATCH NOTHING\n")
        for label, v in dead:
            print(f"  {label}\n      {v}")
        return 1
    print("Every configured value matches at least one row.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
