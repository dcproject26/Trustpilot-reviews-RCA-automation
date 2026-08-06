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


def _machine_resolution(draft) -> str:
    """What the PREFILL would have written into `resolution`.

    api.py fills it from the DSS recommendation's compensation line whenever
    it is empty — `if not d.resolution and dss_rec.get("compensation")`. So a
    non-empty `resolution` says nothing about whether a person wrote one, and
    treating its presence as human work protected every draft in the database
    from the tool whose entire job is rebuilding them: 45 rows reported as
    "human work would be lost", 0 rebuilt, and not one of them touched by a
    human.

    Comparing against the value the machine would have put there is what
    separates the two. A person who edits the text changes it, and it stops
    matching. A person who happens to type the prefill back verbatim loses a
    resolution identical to the one the re-run regenerates, which costs
    nothing.
    """
    rec = getattr(draft, "dss_rec", None)
    if not isinstance(rec, dict):
        return ""
    return str(rec.get("compensation") or "").strip()


def _has_human_work(review, draft) -> list:
    marks = []
    if getattr(review, "status", "") == "sent":
        marks.append("sent")
    if draft is not None:
        if getattr(draft, "sent_at", None):
            marks.append("sent_at")
        # The RCA body itself. Every inline edit in the dashboard writes to
        # rca_v3, and a re-run replaces that column whole - so this is the
        # marker that actually matters, and it was the one not being checked.
        if getattr(draft, "rca_v3_edited_at", None):
            marks.append("rca edited")
        for f in HUMAN_FIELDS:
            val = getattr(draft, f, "")
            val = (val if isinstance(val, str) else "").strip()
            if not val:
                continue
            if f == "resolution" and val == _machine_resolution(draft):
                marks.append("resolution (prefilled — not human)")
                continue
            marks.append(f)
    # A mark that only says "the machine put this here" is not human work.
    # Kept in the list so the count can be reported rather than vanishing.
    return [m for m in marks if "prefilled" not in m]


def _prefilled_only(review, draft) -> bool:
    """Whether this draft's ONLY resolution is the machine's prefill.

    Reported rather than silently skipped: "45 protected" became "0 protected"
    overnight, and a reader deserves to know which rule changed under them.
    """
    if _has_human_work(review, draft):
        return False
    val = getattr(draft, "resolution", "") if draft is not None else ""
    val = (val if isinstance(val, str) else "").strip()
    return bool(val) and val == _machine_resolution(draft)


def _buckets(db) -> Counter:
    from server.db import Review
    from server.tiers import classify
    return Counter(classify(r, r.draft) for r in db.query(Review).all())


def _show(label: str, counts: Counter):
    """One line per run, over EVERY bucket the rule can produce.

    The four names were hard-coded and `processing` was not among them. This
    tool DELETES every draft row and rebuilds it, and `processing` is exactly
    the bucket a review lands in when the rebuild did not finish — no draft
    row, nothing searched. So the one outcome that means "this run failed and
    left reviews worse than it found them" was the one outcome the report did
    not print, while `(total N)` quietly counted it. Seven destroyed drafts
    read as `identified 3 · candidates 1 · untraceable 2 · sent 1 (total 14)`
    and the reader had to notice the arithmetic.

    Driven off server.tiers.BUCKETS so a bucket added later cannot go missing
    the same way, and any bucket outside that list is printed rather than
    dropped — an unknown name is a finding, not something to hide.
    """
    from server.tiers import BUCKETS
    order = [b for b in ("identified", "candidates", "untraceable",
                         "processing", "sent") if b in BUCKETS]
    order += [b for b in BUCKETS if b not in order]
    order += [k for k in sorted(counts) if k not in order]
    total = sum(counts.values())
    print(f"  {label:<10} " + " · ".join(
        f"{k} {counts.get(k, 0)}" for k in order) + f"  (total {total})")


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

        targets, protected, prefilled = [], [], 0
        for r in reviews:
            marks = _has_human_work(r, r.draft)
            if _prefilled_only(r, r.draft):
                prefilled += 1
            if marks and not args.include_edited:
                protected.append((r.id, marks))
            else:
                targets.append(r.id)

        if prefilled:
            # Announce the judgement. These rows LOOK edited - they carry a
            # resolution - and are being rebuilt anyway because the machine is
            # what wrote it.
            print(f"\n  {prefilled} draft(s) carry a resolution the DSS prefill "
                  f"wrote, not a person — rebuilding those")

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
