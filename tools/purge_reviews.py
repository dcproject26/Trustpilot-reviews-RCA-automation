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



# ── the decisions, as functions, so they can be DRIVEN ──────────────────────
# Inline in main() they are reachable only by running the script, and the only
# test possible is a source assertion — the spelling check CLAUDE.md forbids.

def preflight(engine) -> str:
    """"" if this database is usable, else the sentence to refuse with.

    THREE OUTCOMES, NOT TWO. This used to be `try: db.query(Review).count()
    except Exception:` under a comment about naming the wrong-database case —
    and it caught a wrong password, an unreachable host, a TLS failure and an
    expired Neon endpoint all the same way, reporting every one of them as
    "connected, but this database has no `reviews` table". It claimed a
    connection had succeeded without ever checking that one had, which is the
    guard committing the bug it was written to catch.

    It cost a real purge: a redacted password was pasted, nothing connected,
    and the tool said the production database was empty. The `[db] connected
    to ...` banner was missing from that output — the only evidence anything
    was wrong, and it was an absence, which is exactly what nobody notices.

    So: connect, THEN look for the table, and say which of the two failed. The
    driver's own message is quoted rather than summarised, because "password
    authentication failed for user" and "could not translate host name" send
    you to completely different places.
    """
    from sqlalchemy import inspect, text
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
    except Exception as e:
        detail = str(e).strip().splitlines()[0][:300] if str(e).strip() else ""
        return (f"could not connect to this database at all, so nothing is "
                f"known about what it holds.\n"
                f"         The driver said: {type(e).__name__}: {detail}\n"
                f"         A wrong password, an unreachable host, a firewall "
                f"and a suspended endpoint all land here — the line above says "
                f"which. NOTE: a password shown as `npg_...` in chat is "
                f"REDACTED, not the real one.")
    try:
        names = set(inspect(engine).get_table_names())
    except Exception as e:
        return (f"connected, but its schema could not be read: "
                f"{type(e).__name__}: {str(e).strip()[:200]}")
    if "reviews" not in names:
        return (f"connected, but this database has no `reviews` table.\n"
                f"         It holds {len(names)} other table(s): "
                f"{', '.join(sorted(names)[:8]) or '(none at all)'}.\n"
                f"         It is either empty (nothing has run against it) or "
                f"not the database you meant. The banner above says which one "
                f"was opened.")
    return ""


def boundary(db, d, review_id: str):
    """(row, why_not) for the review the purge stops at."""
    row = db.query(d.Review).filter(d.Review.id == review_id).first()
    if row is None:
        total = db.query(d.Review).count()
        return None, (
            f"no review {review_id!r} in this database, which holds {total} "
            f"review(s). A Development database runs beside the Production "
            f"one, so the likeliest cause is the wrong connection rather than "
            f"a wrong id. Re-run with DATABASE_URL set to the database you "
            f"mean.")
    if row.received_at is None:
        return None, (f"{review_id} has no received_at, so 'before' it cannot "
                      f"be decided. Pick another boundary.")
    return row, ""


def reviews_before(db, d, cutoff_at):
    """Reviews STRICTLY before `cutoff_at`, oldest first.

    Strictly: the named review is kept. Deleting the row the caller named as
    the edge is the off-by-one nobody notices until the review they were
    protecting is gone. An undated review is not "before" anything — SQL drops
    NULLs from the comparison anyway, and saying so is the intent.
    """
    return (db.query(d.Review)
              .filter(d.Review.received_at.isnot(None),
                      d.Review.received_at < cutoff_at)
              .order_by(d.Review.received_at.asc()).all())


def purge(db, d, ids=None) -> dict:
    """Delete reviews and their dependents. `ids=None` means everything.

    Children first: rca_drafts.review_id is a foreign key, and an orphaned
    queued run_job has the drain loop reaching for a review that no longer
    exists — the table this tool predated and used to leave behind.
    """
    if ids is not None and not ids:
        return {"reviews": 0, "drafts": 0, "metrics": 0, "jobs": 0}

    def _scope(q, col):
        return q if ids is None else q.filter(col.in_(ids))

    out = {}
    out["metrics"] = _scope(db.query(d.ReviewMetric),
                            d.ReviewMetric.review_id).delete(synchronize_session=False)
    out["jobs"] = _scope(db.query(d.RunJob),
                         d.RunJob.review_id).delete(synchronize_session=False)
    out["drafts"] = _scope(db.query(d.RcaDraft),
                           d.RcaDraft.review_id).delete(synchronize_session=False)
    out["reviews"] = _scope(db.query(d.Review),
                            d.Review.id).delete(synchronize_session=False)
    db.commit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true",
                    help="actually delete; without it nothing is written")
    ap.add_argument("--before", metavar="REVIEW_ID", default="",
                    help="only delete reviews received BEFORE this one; the "
                         "named review is KEPT. Without it, everything goes.")
    args = ap.parse_args()

    import server.db as d
    from server.db import SessionLocal, Review, RcaDraft, ReviewMetric, engine

    # Which database, said out loud. Two environments with separate databases
    # is the failure that makes a purge look like it did nothing: you emptied
    # one and are looking at the other.
    url = engine.url
    target = (url.database if url.get_backend_name().startswith("sqlite")
              else f"{url.host or '?'}/{url.database or '?'}")
    print(f"database: {url.get_backend_name()} {target}")

    # CONNECT BEFORE CLAIMING ANYTHING ABOUT THE CONTENTS. `preflight` separates
    # "never reached it" from "reached it, no schema"; they used to print the
    # same sentence, and the one that was really happening was the first.
    bad = preflight(engine)
    if bad:
        print(f"REFUSING: {bad}")
        return 2

    db = SessionLocal()
    try:
        # ── scope ────────────────────────────────────────────────────────────
        # WHY THIS FLAG LIVES HERE rather than in a second script. A parallel
        # tool was written for the bounded case and it duplicated this one's
        # cascade — which is how the two drifted: this one predates `run_jobs`
        # and left those rows orphaned, so a queued job survived a purge with
        # the drain loop reaching for a review that no longer existed. One
        # tool, one cascade, two scopes.
        if args.before:
            edge, why = boundary(db, d, args.before)
            if edge is None:
                print(f"REFUSING: {why}")
                return 2
            # STRICTLY before — the named review is kept. Deleting the row the
            # caller named as the edge is the off-by-one nobody notices until
            # the review they were protecting is gone.
            doomed = reviews_before(db, d, edge.received_at)
            ids = [r.id for r in doomed]
            print(f"boundary: {edge.id}  {edge.received_at}  ({edge.author})")
            print(f"{len(doomed)} review(s) received before it:\n")
            sent = 0
            for r in doomed:
                mark = ""
                if (r.status or "") == "sent":
                    sent += 1
                    mark = "  << SENT - completed work"
                print(f"  {r.received_at}  {r.id}  "
                      f"{(r.author or '')[:24]:24} {r.status}{mark}")
            if sent:
                print(f"\n{sent} of these are SENT: the reply, the RCA and the "
                      f"confirmation on them do not come back.")
        else:
            ids = None          # everything
            totals = {"reviews":  db.query(Review).count(),
                      "drafts":   db.query(RcaDraft).count(),
                      "metrics":  db.query(ReviewMetric).count(),
                      "jobs":     db.query(d.RunJob).count()}
            for k, v in totals.items():
                print(f"  {k:<10} {v}")
            if not any(totals.values()):
                print("\nNothing to delete - the database is already empty.")
                return 0

        if ids is not None and not ids:
            print("\nnothing to do.")
            return 0

        if not args.apply:
            what = (f"{len(ids)} review(s) and everything hanging off them"
                    if ids is not None else "EVERY review and its drafts, "
                    "metrics and queued jobs")
            print(f"\nDry run. --apply would delete {what}.")
            print("Re-ingesting from Slack brings reviews back, but not the "
                  "replies, RCAs or confirmations made on them.")
            return 0

        # Children first: rca_drafts.review_id is a foreign key, and an
        # orphaned queued run_job has the drain loop reaching for a review that
        # no longer exists.
        counts = purge(db, d, ids)
        n_m, n_j = counts["metrics"], counts["jobs"]
        n_d, n_r = counts["drafts"], counts["reviews"]
        print(f"\ndeleted: {n_m} metric(s), {n_j} job(s), {n_d} draft(s), "
              f"{n_r} review(s)")
        if ids is None:
            after = {"reviews": db.query(Review).count(),
                     "drafts":  db.query(RcaDraft).count(),
                     "metrics": db.query(ReviewMetric).count(),
                     "jobs":    db.query(d.RunJob).count()}
            left = {k: v for k, v in after.items() if v}
            if left:
                # A partial delete that prints a success line is how you end up
                # debugging "stale" rows that were never removed.
                print(f"STILL PRESENT: {left} - the purge did not complete")
                return 1
            print("every table is empty. Ingest and the next draft is produced "
                  "entirely by the code running now.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
