#!/usr/bin/env python3
"""
Wipe every generated draft and rebuild them from scratch, then report what
moved.

    python3 tools/rerun_all.py                  # dry run: what would happen
    python3 tools/rerun_all.py --apply          # do it
    python3 tools/rerun_all.py --apply --keep-sent-edits   # default; see below

This is the honest way to test whether the matching and RCA logic works,
rather than re-running the reviews that already failed and hoping. Every draft
is deleted and regenerated, so the run exercises the whole chain - BID
extraction, Tier 1 verification, the Zendesk indicator search, the BigQuery
cascade, classification, insights, DSS, RCA, response draft - on real data.

What it does NOT delete: the reviews themselves. Those are the source data
(and re-ingesting them from Slack needs the channels:history scope the bot
does not currently have). Reviews are untouched; only generated output is
rebuilt.

Human work is protected by default. A review that has been SENT, or whose
draft carries an edited reply, a hand-written Slack post or a resolution, is
skipped entirely - regenerating it would overwrite something a person wrote
and cannot be recovered by re-running. Pass --include-edited to override that
deliberately.

Prints the bucket distribution before and after, so the effect of a logic fix
is visible as a number rather than an impression.
"""
import argparse
import asyncio
import os
import sys
from collections import Counter
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HUMAN_FIELDS = ("final_response", "slack_thread_override", "resolution")


def _migrate_first():
    """Bring the schema up to the models before querying them.

    These tools are what someone runs BEFORE restarting anything - that is
    the whole point of a diagnostic - so they cannot assume the server has
    already run the migration. Without this, the first command after a pull
    that adds a column dies on "column does not exist" and reads as data
    loss rather than a pending migration. init_db() is idempotent.
    """
    from server.db import init_db
    init_db()


def _has_human_work(review, draft) -> list:
    marks = []
    if getattr(review, "status", "") == "sent":
        marks.append("sent")
    if draft is not None:
        if getattr(draft, "sent_at", None):
            marks.append("sent_at")
        for f in HUMAN_FIELDS:
            if (getattr(draft, f, "") or "").strip():
                marks.append(f)
    return marks


def _buckets(db) -> Counter:
    from server.db import Review
    from server.tiers import classify
    return Counter(classify(r, r.draft) for r in db.query(Review).all())


def _show(label: str, counts: Counter):
    total = sum(counts.values())
    print(f"  {label:<10} " + " · ".join(
        f"{k} {counts.get(k, 0)}" for k in
        ("identified", "candidates", "untraceable", "sent")) + f"  (total {total})")


async def _run(ids, concurrency: int):
    from server.pipeline import process_review
    sem = asyncio.Semaphore(concurrency)
    done = {"n": 0, "failed": []}

    async def one(rid):
        async with sem:
            try:
                await process_review(rid)
            except Exception as e:
                done["failed"].append((rid, f"{type(e).__name__}: {e}"[:200]))
            finally:
                done["n"] += 1
                print(f"    {done['n']}/{len(ids)}  {rid}", flush=True)

    await asyncio.gather(*(one(r) for r in ids))
    return done


def main():
    _migrate_first()
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="actually do it")
    ap.add_argument("--include-edited", action="store_true",
                    help="also rebuild drafts carrying human edits (destroys them)")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    from server.db import SessionLocal, Review, RcaDraft

    db = SessionLocal()
    try:
        reviews = db.query(Review).order_by(Review.received_at.desc()).limit(args.limit).all()
        before = _buckets(db)
        print(f"{len(reviews)} review(s) in the database")
        _show("before", before)

        targets, protected = [], []
        for r in reviews:
            marks = _has_human_work(r, r.draft)
            if marks and not args.include_edited:
                protected.append((r.id, marks))
            else:
                targets.append(r.id)

        if protected:
            print(f"\n  protected ({len(protected)}) - human work would be lost:")
            for rid, marks in protected[:10]:
                print(f"    {rid:<28} {marks}")
            if len(protected) > 10:
                print(f"    … and {len(protected) - 10} more")
            print("    (pass --include-edited to rebuild these too)")

        print(f"\n  would delete and rebuild {len(targets)} draft(s)")
        if not args.apply:
            print("\nDry run - nothing was deleted. Re-run with --apply.")
            return 0

        deleted = (db.query(RcaDraft)
                     .filter(RcaDraft.review_id.in_(targets))
                     .delete(synchronize_session=False)) if targets else 0
        db.commit()
        print(f"\n  deleted {deleted} draft row(s); rebuilding "
              f"{args.concurrency} at a time…")
    finally:
        db.close()

    started = datetime.utcnow()
    result = asyncio.run(_run(targets, args.concurrency))
    took = (datetime.utcnow() - started).total_seconds()

    db = SessionLocal()
    try:
        after = _buckets(db)
    finally:
        db.close()

    print(f"\nrebuilt {len(targets)} review(s) in {int(took)}s")
    _show("before", before)
    _show("after", after)
    moved = {k: after.get(k, 0) - before.get(k, 0)
             for k in set(before) | set(after)}
    print("  change     " + " · ".join(
        f"{k} {v:+d}" for k, v in sorted(moved.items()) if v))
    if result["failed"]:
        print(f"\n  {len(result['failed'])} failed:")
        for rid, err in result["failed"][:10]:
            print(f"    {rid}: {err}")
    else:
        print("\n  no failures")
    return 0


if __name__ == "__main__":
    sys.exit(main())
