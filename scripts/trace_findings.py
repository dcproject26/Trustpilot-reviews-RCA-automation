"""Case findings with their pairwise overlap, so a threshold can be measured.

The dedupe collapses a reworded repeat when containment reaches
`_SAME_FACT_OVERLAP`. Setting that number by guessing is how it has been wrong
twice. This prints every finding on a real card and the overlap between every
pair, so the threshold can be put in the GAP between "same fact" and
"different fact" instead of picked.

    python3 scripts/trace_findings.py tp_abc123        # a review id
    python3 scripts/trace_findings.py --bid 32885089   # or a booking id

Read-only: it reads the stored draft and prints. No Zendesk, no model call,
nothing written.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("review_id", nargs="?", help="review id (tp_...)")
    ap.add_argument("--bid", help="booking id, if you do not have the review id")
    ap.add_argument("--floor", type=float, default=0.25,
                    help="only print pairs at or above this overlap")
    a = ap.parse_args(argv)
    if not a.review_id and not a.bid:
        ap.error("give a review id or --bid")

    from server.db import SessionLocal, RcaDraft
    from server.services.rca_v4_validate import _case_finding_key, _SAME_FACT_OVERLAP
    from server.services.rca_v4_validate import _tokens

    s = SessionLocal()
    try:
        q = s.query(RcaDraft)
        d = (q.filter(RcaDraft.review_id == a.review_id).first() if a.review_id
             else next((r for r in q.all()
                        if str((r.booking or {}).get("id") or "") == str(a.bid)), None))
        if not d:
            print("No draft found for that id.")
            return 1
        v3 = d.rca_v3 if isinstance(d.rca_v3, dict) else {}
        rows = ((v3.get("what_went_wrong") or {}).get("case_findings")) or []
        print(f"\n=== CASE FINDINGS ON {d.review_id}: {len(rows)} ===")
        texts = []
        for i, r in enumerate(rows):
            t = (r or {}).get("text") if isinstance(r, dict) else str(r)
            backs = (r or {}).get("backs_claim") if isinstance(r, dict) else None
            ref = (r or {}).get("ref") if isinstance(r, dict) else None
            texts.append(t or "")
            tag = "narrative" if backs is None else f"backs claim {backs}"
            _t = (r or {}).get("time") if isinstance(r, dict) else None
            # THE FIELD THE ORDER COMES FROM. Every finding on a real card came
            # back with time: null, so §1 was the model's writing order wearing
            # a chronology's clothes. Printed first because it is the thing to
            # look at.
            _tt = str(_t) if _t else "NO TIME — cannot be placed"
            print(f"\n  [{i:>2}] {_tt:<28} {tag}{'  ' + str(ref) if ref else ''}")
            print(f"       {' '.join(str(t or '').split())}")

        _undated = sum(1 for r in rows
                       if isinstance(r, dict) and not (r or {}).get("time"))
        print(f"\n  {_undated} of {len(rows)} carry NO TIME"
              + (" — this section is not a chronology, it is the order the "
                 "model wrote them in" if _undated == len(rows) else ""))

        print(f"\n=== PAIRWISE OVERLAP (threshold now {_SAME_FACT_OVERLAP}) ===")
        print("  containment = shared significant words / the shorter row's words\n")
        pairs = []
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                a_, b_ = _tokens(texts[i]), _tokens(texts[j])
                if not a_ or not b_:
                    continue
                ov = len(a_ & b_) / max(1, min(len(a_), len(b_)))
                if ov >= a.floor:
                    pairs.append((ov, i, j, sorted(a_ & b_)))
        if not pairs:
            print(f"  no pair reaches {a.floor}. Nothing here reads as a repeat.")
        for ov, i, j, shared in sorted(pairs, reverse=True):
            state = "COLLAPSED" if ov >= _SAME_FACT_OVERLAP else "kept apart"
            print(f"  {ov:.2f}  [{i:>2}] x [{j:>2}]  {state}")
            print(f"        shared: {', '.join(shared)}")
        print("\nWhat to read here:")
        print("  A pair you consider a DUPLICATE that says 'kept apart' is the")
        print("  threshold being too high — its number is the ceiling.")
        print("  A pair you consider DIFFERENT that says 'COLLAPSED' is the")
        print("  threshold being too low — its number is the floor.")
        print("  The gap between those two is where the threshold belongs.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    sys.exit(main())
