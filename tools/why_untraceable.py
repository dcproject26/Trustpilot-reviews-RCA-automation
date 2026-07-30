#!/usr/bin/env python3
"""
Say WHY each review is untraceable, one line per review.

    python3 tools/why_untraceable.py
    python3 tools/why_untraceable.py --all        # every review, not just untraceable
    python3 tools/why_untraceable.py --id tp_...  # one review, verbose

The dashboard puts a review in Untraceable whenever match_tier is null, and
that single symptom has at least five different causes:

  no draft row      the pipeline threw before saving - a bug, not a match
                    failure. The booking is only written at the last step, so
                    anything raising earlier loses the whole run.
  no BID, no name   nothing to search with. Correct outcome.
  searched, no hit  BID or signals existed but BigQuery returned nothing.
  candidates only   matches were found but nobody confirmed one.
  bigquery offline  the connector was down, so every match failed for a
                    reason that has nothing to do with the reviews.

Read-only. Run it where the server runs.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def classify(r, d, bq_live: bool) -> tuple[str, str]:
    """(verdict, detail) for one review."""
    if d is None:
        return ("NO DRAFT ROW",
                "pipeline threw before the save step - check the server log for "
                "'Pipeline error' on this id")
    tier = d.match_tier
    if tier is not None:
        return ("matched", f"tier {tier} ({d.match_confidence or '?'}) "
                           f"bid {(d.booking or {}).get('id', '?')}")
    if d.candidate_state or (d.candidates_list or []):
        return ("CANDIDATES, UNCONFIRMED",
                f"{len(d.candidates_list or [])} candidate(s) waiting for an "
                f"associate to confirm")
    has_bid = bool(r.reference_number)
    sig = d.extracted_signals or {}
    has_sig = bool(sig.get("author_first") or sig.get("author_last")
                   or sig.get("venue_hints"))
    attempts = d.narrowing_attempts or []
    if not bq_live:
        return ("BIGQUERY OFFLINE",
                "no match was possible for any review while the connector is down")
    if has_bid and not attempts:
        return ("BID PRESENT BUT NOT SEARCHED",
                f"reference_number {r.reference_number} on the review and zero "
                f"narrowing attempts - the match step did not run")
    if attempts:
        return ("SEARCHED, NO HIT",
                f"{len(attempts)} narrowing attempt(s), all empty; "
                f"bid={r.reference_number or '-'} signals={'yes' if has_sig else 'no'}")
    if not has_bid and not has_sig:
        return ("NOTHING TO SEARCH WITH",
                "no BID in the review text and no usable name/venue signal - "
                "this is the correct outcome, not a failure")
    return ("UNKNOWN",
            f"bid={r.reference_number or '-'} signals={'yes' if has_sig else 'no'} "
            f"attempts=0 draft_generated_at={d.generated_at}")


def run_inline(review_id: str) -> int:
    """Run the pipeline for ONE review in the foreground, with logging on and
    the traceback printed. Background tasks swallow their exception into a log
    line nobody reads; this puts the failure on screen."""
    import asyncio
    import logging
    import traceback
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
        force=True)
    from server.pipeline import process_review
    print(f"\n=== running the pipeline inline for {review_id} ===\n")
    try:
        asyncio.run(process_review(review_id))
        print("\n=== pipeline returned without raising ===")
    except Exception:
        print("\n=== PIPELINE RAISED - this is the cause ===")
        traceback.print_exc()
        return 2

    from server.db import SessionLocal, Review
    db = SessionLocal()
    try:
        r = db.query(Review).filter(Review.id == review_id).first()
        d = r.draft if r else None
        if not d:
            print("\nStill NO DRAFT ROW after a clean run - the save never "
                  "happened. That is a bug, not a match failure.")
            return 3
        print(f"\nmatch_tier      : {d.match_tier}")
        print(f"match_method    : {d.match_method}")
        print(f"booking         : {(d.booking or {}).get('id') or '(none)'}"
              f"{' [UNVERIFIED]' if (d.booking or {}).get('_unverified') else ''}")
        print(f"candidates      : {len(d.candidates_list or [])}")
        print(f"untraceable_why : {(d.extracted_signals or {}).get('untraceable_reason') or '-'}")
        print(f"\nconfidence trail ({len(d.confidence_trail or [])} step(s)):")
        import re as _re
        for step in (d.confidence_trail or []):
            print(f"  [{step.get('mark', '?'):<4}] "
                  f"{_re.sub(r'<[^>]+>', '', step.get('text', ''))}")
        print(f"\nextracted signals:")
        for k, v in (d.extracted_signals or {}).items():
            print(f"  {k}: {str(v)[:120]}")
        print(f"\nnarrowing attempts ({len(d.narrowing_attempts or [])}):")
        for a in (d.narrowing_attempts or []):
            print(f"  {a}")
    finally:
        db.close()
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="include matched reviews")
    ap.add_argument("--id", default="", help="one review id, verbose")
    ap.add_argument("--run", default="",
                    help="run the pipeline for this review id in the "
                         "foreground and print the traceback + trail")
    args = ap.parse_args()

    if args.run:
        return run_inline(args.run)

    from server.config import is_live
    from server.db import SessionLocal, Review

    bq_live = is_live("bigquery")
    print(f"bigquery live: {bq_live}   zendesk live: {is_live('zendesk')}   "
          f"anthropic live: {is_live('anthropic')}")

    db = SessionLocal()
    try:
        q = db.query(Review).order_by(Review.received_at.desc())
        if args.id:
            q = q.filter(Review.id == args.id)
        reviews = q.all()
        if not reviews:
            print("no reviews in the database")
            return 1

        counts: dict[str, int] = {}
        print(f"\n{'review id':<26}{'ref':<12}{'draft':<7}{'tier':<6}verdict")
        print("-" * 110)
        for r in reviews:
            d = r.draft
            verdict, detail = classify(r, d, bq_live)
            counts[verdict] = counts.get(verdict, 0) + 1
            if verdict == "matched" and not (args.all or args.id):
                continue
            print(f"{r.id[:25]:<26}{str(r.reference_number or '-')[:11]:<12}"
                  f"{('yes' if d else 'NO'):<7}"
                  f"{str(d.match_tier if d else '-'):<6}{verdict}")
            print(f"{'':<51}{detail}")
            if args.id and d:
                print(f"\n  booking:            {(d.booking or {}).get('id') or '(none)'}")
                print(f"  candidates:         {len(d.candidates_list or [])}")
                print(f"  narrowing attempts: {d.narrowing_attempts or []}")
                print(f"  extracted signals:  {d.extracted_signals or {}}")
                print(f"  bid_source:         {d.bid_source}")
                print(f"  generated_at:       {d.generated_at}")
                print(f"  l1/l2:              {d.l1} / {d.l2}")
                print(f"  has rca_v3:         {bool(d.rca_v3)}")
                print(f"  confidence trail:   {len(d.confidence_trail or [])} step(s)")
                for step in (d.confidence_trail or [])[:12]:
                    print(f"     - {step}")

        print("-" * 110)
        print("summary:")
        for k, v in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {v:>4}  {k}")
        if counts.get("NO DRAFT ROW"):
            print("\nNO DRAFT ROW is the one that means a bug rather than a miss: "
                  "the pipeline raised before its save step. Grep the server log "
                  "for 'Pipeline error' to see what raised.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
