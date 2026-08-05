#!/usr/bin/env python3
"""Delete every review and everything generated from it. Then re-ingest.

    python3 tools/purge_reviews.py            # dry run: counts only
    python3 tools/purge_reviews.py --apply    # do it

WHY THIS EXISTS SEPARATELY FROM rerun_all.py. That tool rebuilds drafts and
keeps the reviews, which is the right move when the reviews are worth keeping.
This one is for the other case: you want a clean database so that what appears
next was demonstrably produced by the code running now, with no stored artifact
from an older build left to explain it away.

THE REVIEWS DO NOT COME BACK. Re-ingesting them from Slack needs the
channels:history scope the bot does not have, so anything deleted here has to
arrive again through the normal inbound path. That is the whole point when you
are testing the flow end to end, and a disaster if you meant rerun_all.

Deletes children before parents - review_metrics, then rca_drafts, then
reviews - because rca_drafts.review_id is a foreign key and the reverse order
fails halfway, leaving drafts whose review is gone.

Every count is printed, including the ones that were already zero. "Deleted 0
metrics" and "did not look at metrics" are different facts.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is written")
    args = ap.parse_args()

    from server.db import SessionLocal, Review, RcaDraft, ReviewMetric, engine

    # Which database, said out loud. Two environments with separate databases
    # is the failure that makes a purge look like it did nothing: you emptied
    # one and are looking at the other.
    url = engine.url
    target = (url.database if url.get_backend_name().startswith("sqlite")
              else f"{url.host or '?'}/{url.database or '?'}")
    print(f"database: {url.get_backend_name()} {target}")

    db = SessionLocal()
    try:
        before = {"reviews":  db.query(Review).count(),
                  "drafts":   db.query(RcaDraft).count(),
                  "metrics":  db.query(ReviewMetric).count()}
        for k, v in before.items():
            print(f"  {k:<10} {v}")

        if not any(before.values()):
            print("\nNothing to delete - the database is already empty.")
            return 0

        if not args.apply:
            print(f"\nDry run. --apply would delete "
                  f"{before['metrics']} metric(s), {before['drafts']} draft(s) "
                  f"and {before['reviews']} review(s).")
            print("The reviews CANNOT be restored - they have to be ingested "
                  "again through Slack.")
            return 0

        # Children first: rca_drafts.review_id is a foreign key.
        n_m = db.query(ReviewMetric).delete(synchronize_session=False)
        n_d = db.query(RcaDraft).delete(synchronize_session=False)
        n_r = db.query(Review).delete(synchronize_session=False)
        db.commit()

        after = {"reviews":  db.query(Review).count(),
                 "drafts":   db.query(RcaDraft).count(),
                 "metrics":  db.query(ReviewMetric).count()}
        print(f"\ndeleted: {n_m} metric(s), {n_d} draft(s), {n_r} review(s)")
        left = {k: v for k, v in after.items() if v}
        if left:
            # A partial delete that prints a success line is how you end up
            # debugging "stale" rows that were never removed.
            print(f"STILL PRESENT: {left} - the purge did not complete")
            return 1
        print("all three tables are empty. Ingest and the next draft is "
              "produced entirely by the code running now.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
