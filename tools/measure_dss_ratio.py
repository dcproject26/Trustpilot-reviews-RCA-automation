"""Measure the keyword-fallback ratio distribution across the real DSS export.

The keyword scorer that runs when the AI selector is unavailable takes any
positive score as a match. On the 117-row export, `cancelation` is 63 rows
(54%) with A/B/C-column triplicates of most selectors, so a single common
word ("issues", "cancel") lands a coincidence-level score-1 hit on a
cancelation row for the majority of outage cases. The fix is to reject
matches whose ratio (overlap / selector-keyword-count) is below a cut.

This script picks the cut from the real data. It computes the ratio each
row would score against two synthetic populations:

  * COINCIDENCE reviews - a single common word, meant to hit spuriously.
  * REAL-MATCH reviews - taken from existing test fixtures, meant to hit
    correctly and represent the shape of a genuine terse-scenario match.

It prints both distributions and the gap between them. The picked cut goes
into MIN_KEYWORD_RATIO in server/services/dss.py. Re-run this script if the
export grows or the token vocabulary shifts.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
from collections import Counter

# Import _keywords and _sel_col from the real module rather than duplicating
# them - a second copy would drift.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from server.services.dss import _keywords, TABS  # noqa: E402

UNIFIED_PATH = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "content", "dss_unified.json")


def _sel_col_for(tab_type: str) -> str:
    """The column the scorer reads for this tab. Mirrors dss._sel_col but
    keyed on the type name the export carries."""
    return TABS.get(tab_type, "scenarios")


def load_rows() -> list[dict]:
    """Every row from the export, flattened, each with its selector text."""
    with open(UNIFIED_PATH, encoding="utf-8") as fh:
        payload = json.load(fh)
    out = []
    for tab in (payload.get("tabs") or {}).values():
        ttype = tab.get("type") or "other"
        col = _sel_col_for(ttype)
        for row in tab.get("rows") or []:
            selector = (row.get("selector") or "").strip()
            if not selector:
                continue
            out.append({
                "type":     ttype,
                "column":   row.get("column") or "",
                "selector": selector,
                "sel_col":  col,
                "sel_kw":   _keywords(selector),
            })
    return out


# Words a real 1-star review very plausibly carries but that on their own
# say nothing about which scenario applies. Picked from the highest-frequency
# >=4-letter tokens across the selectors themselves - the ones most likely
# to spuriously intersect. "issue" is stopworded via _keywords? no - only
# in the pluralised form; both are exercised.
COINCIDENCE_WORDS = [
    "cancel", "cancellation", "cancelled",
    "issue", "issues",
    "guide", "point", "delay", "delayed",
    "ticket", "tickets", "reservation",
    "meeting", "venue", "closed", "late",
    "refund", "vendor", "service",
    "experience", "wrong", "email", "confirmation",
    "help", "problem", "money", "waiting",
]

# Real matches - each review is chosen because a NATURAL target scenario
# exists in the 117-row export (the exact selector strings appear in
# content/dss_unified.json). The point is to measure the ratio real reviews
# hit AGAINST THE REAL EXPORT, not against fixture rows built for tests.
# Each tuple: (review-text, expected-tab, note).
REAL_MATCH_REVIEWS = [
    # The exact review from test_dss_unified.py that the absolute-2
    # threshold would have refused. Target: "Guide not present at MP"
    # (kw guide/present, len 2). Overlap {guide} = 1, ratio 0.500.
    ("the guide was not at the meeting point",
     "meetingPointIssue", "terse-scenario, one-word overlap"),
    # Target: "Cx running late" (kw running/late, len 2). Overlap
    # {running, late} = 2, ratio 1.000.
    ("we were running late and missed the tour",
     "meetingPointIssue", "two-word overlap on short selector"),
    # Target: "Tour started late / guide arrived late at MP"
    # (kw tour/started/late/guide/arrived, len 5). Overlap
    # {tour, started, late} = 3, ratio 0.600.
    ("the tour started 40 minutes late and the guide arrived late",
     "supplyPartnerIssue", "three-word overlap"),
    # Target: "Tickets are not sent/unfulfilled booking"
    # (kw tickets/sent/unfulfilled, len 3, "booking" is stopworded).
    # Overlap {tickets, sent} = 2, ratio 0.667.
    ("my tickets were never sent before the visit",
     "delay_fulfilment", "two-word overlap on 3-kw selector"),
    # Target: "Guest did not see booking confirmation email/tickets"
    # (kw confirmation/email/tickets, len 3, "guest"/"booking" stopworded).
    # Overlap {confirmation, email} = 2, ratio 0.667.
    ("I never received the confirmation email with my tickets",
     "cancelation", "two-word overlap, real cancellation scenario"),
    # Target: "HO Error wrong meeting point" (kw error/meeting/point/wrong,
    # len 4). Overlap {wrong, meeting, point} = 3, ratio 0.750.
    ("the confirmation gave the wrong meeting point address",
     "meetingPointIssue", "three-word overlap"),
]


def ratio(overlap: int, sel_kw_len: int) -> float:
    return overlap / sel_kw_len if sel_kw_len else 0.0


def score_all(rows, review_text: str, l2: str = "") -> list[tuple[dict, int, float]]:
    """For a review, return (row, overlap, ratio) for every row that overlaps
    (overlap >= 1). L2-substring hits are marked with ratio = float("inf")
    so they always sort to the top and are excluded from the ratio distribution
    (the design accepts them by an override, not by the ratio)."""
    r_kw = _keywords(f"{l2} {review_text}")
    out = []
    for row in rows:
        overlap = len(r_kw & row["sel_kw"])
        if overlap == 0:
            continue
        if l2 and l2.lower() in row["selector"].lower():
            out.append((row, overlap, float("inf")))
        else:
            out.append((row, overlap, ratio(overlap, len(row["sel_kw"]))))
    return out


def top_hit_ratio(rows, review_text: str, l2: str = "") -> float | None:
    """The RATIO of the row that would win (highest raw overlap)."""
    hits = score_all(rows, review_text, l2)
    if not hits:
        return None
    hits.sort(key=lambda t: (-t[1], t[2]))  # highest overlap first, then ratio
    return hits[0][2]


def describe(name: str, values: list[float]) -> None:
    finite = [v for v in values if v != float("inf")]
    inf_n  = sum(1 for v in values if v == float("inf"))
    print(f"\n{name}: n={len(values)}"
          f" (finite={len(finite)}, L2-substring overrides={inf_n})")
    if not finite:
        print("  no finite ratios")
        return
    finite.sort()
    q = lambda p: finite[max(0, min(len(finite) - 1, int(p * len(finite))))]
    print(f"  min={finite[0]:.3f}  p25={q(0.25):.3f}  "
          f"median={statistics.median(finite):.3f}  "
          f"p75={q(0.75):.3f}  max={finite[-1]:.3f}")
    # Histogram in 0.1 buckets.
    buckets = Counter()
    for v in finite:
        buckets[min(10, int(v * 10))] += 1
    print("  histogram (each * = 1 sample):")
    for b in range(0, 11):
        lo = b / 10; hi = (b + 1) / 10
        n = buckets.get(b, 0)
        bar = "*" * min(n, 60)
        print(f"    {lo:0.1f}-{hi:0.1f}  {n:4d}  {bar}")


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--verbose", action="store_true",
                   help="print every winning row per coincidence word")
    args = p.parse_args()

    rows = load_rows()
    print(f"loaded {len(rows)} rows from {UNIFIED_PATH}")
    by_type = Counter(r["type"] for r in rows)
    print(f"by type: {dict(by_type)}")

    # COINCIDENCE distribution: one word at a time, no L2 (so no substring
    # override); the ratio of the winning row per word.
    coin_ratios: list[float] = []
    for w in COINCIDENCE_WORDS:
        top = top_hit_ratio(rows, w, l2="")
        if top is None:
            print(f"  coincidence {w!r}: no overlap with any row")
            continue
        coin_ratios.append(top)
        if args.verbose:
            hits = sorted(score_all(rows, w, l2=""),
                          key=lambda t: (-t[1], t[2]))[:3]
            for row, ov, r in hits:
                print(f"  coincidence {w!r} -> {row['type']} "
                      f"{row['selector']!r}  overlap={ov} "
                      f"sel_kw={sorted(row['sel_kw'])} ratio={r:.3f}")

    describe("COINCIDENCE (single common word)", coin_ratios)

    # REAL-MATCH distribution: full review text, expected tab, no L2 (so the
    # override is not exercised - we want the raw ratio these should hit).
    real_ratios: list[float] = []
    for review, expected_type, needle in REAL_MATCH_REVIEWS:
        # Filter to rows on the expected tab so we don't cross tabs (in the
        # real code the tab filter narrows first; here we approximate).
        tab_rows = [r for r in rows if r["type"] == expected_type]
        top = top_hit_ratio(tab_rows, review, l2="")
        if top is None:
            print(f"  real-match {review!r}: NOTHING HIT on tab={expected_type}")
            continue
        real_ratios.append(top)
        if args.verbose:
            hits = sorted(score_all(tab_rows, review, l2=""),
                          key=lambda t: (-t[1], t[2]))[:3]
            for row, ov, r in hits:
                print(f"  real-match {review!r}  ->  {row['selector']!r}  "
                      f"overlap={ov} sel_kw_len={len(row['sel_kw'])} "
                      f"ratio={r:.3f}")

    describe("REAL MATCH (test-fixture reviews)", real_ratios)

    # THE CUT is any value strictly BETWEEN max(coincidence) and
    # min(real-match). If the two ranges overlap we say so; that means the
    # ratio alone cannot separate them and the fix needs another signal.
    if not coin_ratios or not real_ratios:
        print("\nnot enough data to derive a cut")
        return 1
    c_max = max(coin_ratios)
    r_min = min(real_ratios)
    print(f"\nCOINCIDENCE max ratio: {c_max:.3f}")
    print(f"REAL-MATCH  min ratio: {r_min:.3f}")
    if c_max < r_min:
        gap = r_min - c_max
        # Midpoint, rounded to 2 dp so the constant is human-readable.
        cut = round((c_max + r_min) / 2, 2)
        print(f"gap: {gap:.3f}  -> recommended MIN_KEYWORD_RATIO = {cut}")
    else:
        print("OVERLAP: the ratio alone cannot separate coincidence from real "
              "matches with these populations. Do not pick a cut - add "
              "another signal, or widen the populations.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
