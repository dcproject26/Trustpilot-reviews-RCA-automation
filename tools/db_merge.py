#!/usr/bin/env python3
"""
Copy reviews and RCA drafts from one database into another, without losing
human work.

    # look first - nothing is written
    python3 tools/db_merge.py --from "$OLD_DATABASE_URL" --to "$NEW_DATABASE_URL"

    # then do it
    python3 tools/db_merge.py --from "$OLD" --to "$NEW" --apply

Why this exists: the workspace and the published deployment ended up on two
different Postgres servers, each holding part of the work. Picking one deletes
nothing, but the other becomes invisible to the app - so before switching, the
rows worth keeping have to move.

What counts as worth keeping. AI output (match, timeline, insights, RCA,
response draft) is REGENERABLE: a re-run rebuilds it from BigQuery, Zendesk
and the model. Human work is not:

    final_response          the associate's edited reply
    sent_at / status=sent   already sent to the guest
    slack_thread_override   the hand-edited Slack post
    resolution              the resolution the associate wrote
    edit_count > 0          any inline edit recorded in review_metrics

A source row wins ONLY when it carries human work the target row lacks, or
when the target has no such row at all. Otherwise the target is left alone -
copying a fresher AI draft over an edited one would destroy the edit, and a
re-run can recreate the AI draft anyway.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

HUMAN_FIELDS = ("final_response", "slack_thread_override", "resolution")


def _engine(url: str):
    from sqlalchemy import create_engine
    return create_engine(url.replace("postgres://", "postgresql://", 1),
                         pool_pre_ping=True)


def _human_marks(review, draft, metric) -> list[str]:
    """Every sign a person touched this review."""
    marks = []
    if review is not None and getattr(review, "status", "") == "sent":
        marks.append("sent")
    if draft is not None:
        if getattr(draft, "sent_at", None):
            marks.append("sent_at")
        for f in HUMAN_FIELDS:
            if (getattr(draft, f, "") or "").strip():
                marks.append(f)
    if metric is not None and (getattr(metric, "edit_count", 0) or 0) > 0:
        marks.append(f"edits={metric.edit_count}")
    return marks


def _copy_columns(model, src_obj, dst_obj):
    for col in model.__table__.columns:
        if col.name == "id":
            continue
        setattr(dst_obj, col.name, getattr(src_obj, col.name))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="src", required=True, help="source DATABASE_URL")
    ap.add_argument("--to", dest="dst", required=True, help="target DATABASE_URL")
    ap.add_argument("--apply", action="store_true",
                    help="actually write; without it nothing is changed")
    ap.add_argument("--force-source", action="store_true",
                    help="overwrite target rows even when the target carries "
                         "human work the source lacks (rarely what you want)")
    args = ap.parse_args()

    if args.src.strip() == args.dst.strip():
        print("source and target are the same database - nothing to do")
        return 1

    from sqlalchemy.orm import sessionmaker
    from server.db import Base, Review, RcaDraft, ReviewMetric

    se, de = _engine(args.src), _engine(args.dst)
    print(f"source : {se.url.host}/{se.url.database}")
    print(f"target : {de.url.host}/{de.url.database}")
    if args.apply:
        # Make sure the target has every table and column before writing.
        Base.metadata.create_all(bind=de)
        import server.db as _db
        _orig = _db.engine
        try:
            _db.engine = de
            _db._ensure_columns()
        finally:
            _db.engine = _orig

    S, D = sessionmaker(bind=se)(), sessionmaker(bind=de)()
    try:
        src_reviews = {r.id: r for r in S.query(Review).all()}
        dst_reviews = {r.id: r for r in D.query(Review).all()}
        src_drafts = {d.review_id: d for d in S.query(RcaDraft).all()}
        dst_drafts = {d.review_id: d for d in D.query(RcaDraft).all()}
        src_metrics = {m.review_id: m for m in S.query(ReviewMetric).all()}
        dst_metrics = {m.review_id: m for m in D.query(ReviewMetric).all()}

        print(f"\nsource: {len(src_reviews)} reviews, {len(src_drafts)} drafts")
        print(f"target: {len(dst_reviews)} reviews, {len(dst_drafts)} drafts")

        plan = {"new": [], "updated": [], "kept": [], "human_conflict": []}
        for rid, sr in src_reviews.items():
            sd, sm = src_drafts.get(rid), src_metrics.get(rid)
            dr, dd, dm = dst_reviews.get(rid), dst_drafts.get(rid), dst_metrics.get(rid)
            s_human = _human_marks(sr, sd, sm)
            d_human = _human_marks(dr, dd, dm)

            if dr is None:
                plan["new"].append((rid, s_human))
                continue
            if d_human and not args.force_source:
                # The target row has human work. Never overwrite it - that work
                # cannot be regenerated, the AI half can.
                plan["human_conflict" if s_human else "kept"].append(
                    (rid, f"target has {d_human}"
                          + (f", source has {s_human}" if s_human else "")))
                continue
            if s_human or (sd is not None and dd is None):
                plan["updated"].append((rid, s_human or ["draft present in source only"]))
            else:
                plan["kept"].append((rid, ["both AI-only; target kept"]))

        for label, rows in plan.items():
            print(f"\n{label.upper()} ({len(rows)}):")
            for rid, why in rows[:20]:
                print(f"  {rid:<28} {why}")
            if len(rows) > 20:
                print(f"  … and {len(rows) - 20} more")

        if plan["human_conflict"]:
            print("\nHUMAN_CONFLICT means BOTH sides were edited by a person. "
                  "Those rows are left untouched; resolve them by hand, or pass "
                  "--force-source once you have decided the source wins.")

        if not args.apply:
            print("\nDry run - nothing was written. Re-run with --apply.")
            return 0

        written = 0
        for rid, _ in plan["new"] + plan["updated"]:
            sr, sd, sm = src_reviews[rid], src_drafts.get(rid), src_metrics.get(rid)
            dr = dst_reviews.get(rid)
            if dr is None:
                dr = Review(id=rid)
                D.add(dr)
            _copy_columns(Review, sr, dr)
            if sd is not None:
                dd = dst_drafts.get(rid)
                if dd is None:
                    dd = RcaDraft(id=sd.id, review_id=rid)
                    D.add(dd)
                _copy_columns(RcaDraft, sd, dd)
            if sm is not None and dst_metrics.get(rid) is None:
                dm = ReviewMetric(review_id=rid)
                D.add(dm)
                _copy_columns(ReviewMetric, sm, dm)
            written += 1
        D.commit()
        print(f"\nwrote {written} review(s) into "
              f"{de.url.host}/{de.url.database} at {datetime.utcnow().isoformat()}Z")
        print("Nothing was deleted from the source - it is still there if this "
              "needs to be repeated or reversed.")
        return 0
    finally:
        S.close()
        D.close()


if __name__ == "__main__":
    sys.exit(main())
