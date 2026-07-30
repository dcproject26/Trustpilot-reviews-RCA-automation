#!/usr/bin/env python3
"""
Show which RCA sections actually came back populated, per review.

    python3 tools/inspect_rca.py                 # one row per review
    python3 tools/inspect_rca.py --id tp_17…     # full dump for one review
    python3 tools/inspect_rca.py --empty logs    # only reviews missing that section

A section that renders blank in the dashboard has two possible causes and
they need different fixes: the model returned nothing for it, or the model
returned it and the client failed to draw it. This reads the stored JSON
directly, so it answers the first question without touching the UI - if the
key is empty here, the model is the reason, and the prompt is what to change.

The summary row marks each section · = present, - = empty. Reading down a
column shows whether a gap is one bad draft or every draft, which is the
difference between re-running one review and rewriting a prompt.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# key in rca_v3 -> short column label
SECTIONS = [
    ("tldr",                 "tldr"),
    ("what_went_wrong",      "wwr"),
    ("booking_logs",         "logs"),
    ("flags",                "flags"),
    ("sop_compliance",       "sop"),
    ("support_interaction",  "supp"),
    ("sp_interaction",       "sp"),
    ("issue_specific_answers", "isa"),
    ("area_of_improving",    "aoi"),
    ("takedown",             "takedn"),
    ("prevention",           "prev"),
]


def _filled(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return bool(str(v).strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", help="dump one review's rca_v3 in full")
    ap.add_argument("--empty", help="list only reviews where this section is empty")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    from server.db import SessionLocal, Review
    from server.tiers import classify

    s = SessionLocal()
    try:
        q = s.query(Review).order_by(Review.received_at.desc())
        if args.id:
            q = q.filter(Review.id == args.id)
        reviews = q.limit(args.limit).all()

        if args.id:
            if not reviews:
                print(f"no review with id {args.id}")
                return 1
            r = reviews[0]
            d = r.draft
            print(f"{r.id}  {r.author}  bucket={classify(r, d)}")
            if d is None:
                print("  no draft row at all - the pipeline never committed one")
                return 0
            bid = (d.booking or {}).get("id")
            print(f"  booking={bid or 'none'}  candidates={len(d.candidates_list or [])}"
                  f"  confirmed={d.selected_candidate_bid or 'no'}")
            v3 = d.rca_v3 or {}
            if not v3:
                print("  rca_v3 is empty - generation failed or was never run")
                return 0
            print(json.dumps(v3, indent=2, ensure_ascii=False)[:12000])
            return 0

        labels = [lbl for _, lbl in SECTIONS]
        print(f"{'review':<26} {'bucket':<12} " + " ".join(f"{l:<6}" for l in labels))
        print("-" * (26 + 13 + 7 * len(labels)))
        missing = {lbl: 0 for lbl in labels}
        shown = 0
        for r in reviews:
            d = r.draft
            v3 = (d.rca_v3 or {}) if d else {}
            marks = []
            for key, lbl in SECTIONS:
                ok = _filled(v3.get(key))
                if not ok:
                    missing[lbl] += 1
                marks.append(("·" if ok else "-").ljust(6))
            if args.empty and _filled(v3.get(
                    next((k for k, l in SECTIONS if l == args.empty), args.empty))):
                continue
            shown += 1
            who = (r.author or "?")[:24]
            print(f"{who:<26} {classify(r, d):<12} " + " ".join(marks))

        print("-" * (26 + 13 + 7 * len(labels)))
        print(f"{'EMPTY of ' + str(len(reviews)):<26} {'':<12} " +
              " ".join(str(missing[l]).ljust(6) for l in labels))
        if args.empty:
            print(f"\n{shown} review(s) with an empty '{args.empty}' section")
        print("\nA column empty for every review is a prompt problem, not a data "
              "problem.\nRun with --id <review_id> to see one draft in full.")
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
