#!/usr/bin/env python3
"""
Map our L1/L2 framework onto the L2 spellings fct_reviews actually stores.

    python3 tools/map_l2.py

Run on Replit. One query, then all matching happens locally.

fct_reviews.issues is written by Headout's own review classifier. The L1/L2
framework this system classifies into is a different vocabulary. They overlap,
they are not the same list, and wherever they diverge the "similar reviews"
count reads zero however correct the mapping looks - which is indistinguishable
on the dashboard from an experience with no history.

This prints three things:

  1. Every L2 in our framework, what it currently searches, and whether any of
     those spellings is live.
  2. For the ones that match nothing, the closest live candidates - scored, so
     an obvious rename is obvious and a coincidence is not.
  3. Every live value that nothing in our framework maps to. This is the list
     that matters: it is what exists and is being ignored.

Nothing is written. Candidates are suggestions to review, not a mapping to
apply blind - a wrong alias silently attributes one issue's history to another,
which is worse than a zero.
"""
import os
import sys
from difflib import SequenceMatcher

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server.services import insights as I           # noqa: E402
from server.services import bq_connector as BQ      # noqa: E402
from server.taxonomy import L2_OPTIONS              # noqa: E402

DAYS = 180
_STOP = {"issue", "issues", "the", "a", "of", "and", "or", "not", "to", "by"}


def live_l2_values():
    cols = BQ.column_types(I._REVIEWS_TABLE)
    date_col = next((c for c in ("reviewed_at", "review_created_at", "created_at")
                     if c in cols), None)
    where = (f"WHERE DATE(r.{date_col}) >= "
             f"DATE_SUB(CURRENT_DATE(), INTERVAL {DAYS} DAY)") if date_col else ""
    rows = BQ.run_query(f"""
SELECT l2v AS v, COUNT(*) AS n
FROM `{I._REVIEWS_TABLE}` r
LEFT JOIN UNNEST(r.issues) AS iss
LEFT JOIN UNNEST(iss.l2_issues) AS l2v
{where}
GROUP BY v
ORDER BY n DESC
""")
    return {r["v"]: r["n"] for r in rows if r["v"]}, date_col


def tokens(s):
    return {t for t in I._norm(s).replace("/", " ").replace("-", " ").split()
            if t and t not in _STOP}


def score(ours, live):
    """
    Blend of token overlap and character similarity.

    Neither alone is enough: token overlap alone rates "Guide Behaviour Issues"
    against "Guide no show" too highly on the shared "guide", and character
    similarity alone misses a genuine rename that reorders words.
    """
    a, b = tokens(ours), tokens(live)
    jac = len(a & b) / len(a | b) if (a | b) else 0.0
    seq = SequenceMatcher(None, I._norm(ours), I._norm(live)).ratio()
    return round(0.6 * jac + 0.4 * seq, 3)


def main():
    if not BQ.available():
        print("No BigQuery connection on this machine.\n"
              "Run this on Replit, where the connector is bound.")
        return 2

    live, date_col = live_l2_values()
    print(f"fct_reviews, last {DAYS} days on {date_col}: "
          f"{len(live)} distinct l2_issue values, {sum(live.values()):,} rows\n")

    ours = [(l1, l2) for l1, l2s in L2_OPTIONS.items() for l2 in l2s]
    live_norm = {I._norm(v): (v, n) for v, n in live.items()}
    claimed = set()

    print("=" * 78)
    print("OUR L2 FRAMEWORK - what each one currently finds")
    print("=" * 78)
    unmatched = []
    for l1, l2 in ours:
        vs = I.l2_variants(l2)
        hits = [(live_norm[v][0], live_norm[v][1]) for v in vs if v in live_norm]
        claimed.update(v for v in vs if v in live_norm)
        if hits:
            total = sum(n for _, n in hits)
            print(f"\n  {l2}   [{l1}]   -> {total:,} rows")
            for v, n in sorted(hits, key=lambda x: -x[1]):
                print(f"        {n:>7,}  {v}")
        else:
            unmatched.append((l1, l2, vs))

    print("\n" + "=" * 78)
    print(f"NO LIVE MATCH ({len(unmatched)}) - these read zero for every booking")
    print("=" * 78)
    for l1, l2, vs in unmatched:
        cands = sorted(((score(l2, v), v, n) for v, n in live.items()),
                       reverse=True)[:4]
        print(f"\n  {l2}   [{l1}]")
        print(f"        searches: {vs}")
        for sc, v, n in cands:
            flag = "  <- likely" if sc >= 0.55 else ""
            print(f"        {sc:.2f}  {n:>7,}  {v}{flag}")

    print("\n" + "=" * 78)
    rest = sorted(((n, v) for v, n in live.items() if I._norm(v) not in claimed),
                  reverse=True)
    print(f"LIVE IN THE WAREHOUSE, MAPPED BY NOTHING ({len(rest)}) - "
          f"{sum(n for n, _ in rest):,} rows ignored")
    print("=" * 78)
    for n, v in rest:
        best = max(((score(l2, v), l2) for _, l2 in ours), default=(0, "-"))
        hint = f"   closest of ours: {best[1]!r} ({best[0]:.2f})" if best[0] >= 0.45 else ""
        print(f"  {n:>7,}  {v}{hint}")

    print("\n" + "=" * 78)
    print("Paste this whole output back. Entries go into _L2_LIVE_ALIASES in "
          "insights.py,\nwhich is additive - _L2_BUCKETS is not touched.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
