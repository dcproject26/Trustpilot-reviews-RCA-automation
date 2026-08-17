"""Delete every review received before a given one, and everything hanging off it.

    python3 tools/purge_reviews_before.py tp_1786790990_301059            # dry run
    python3 tools/purge_reviews_before.py tp_1786790990_301059 --apply    # delete

DRY RUN BY DEFAULT, and that is not politeness. There is no other delete path
in this codebase, nothing here is undoable from inside the app, and the boundary
is a timestamp read off one row — if that row is the wrong one, the wrong half of
the inbox goes. So the default prints exactly what would be removed and touches
nothing.

WHAT COMES BACK AND WHAT DOES NOT. Slack is the source of truth for the reviews
themselves, so `POST /api/reviews/refresh-slack?hours=N` re-ingests them. The
work done ON them does not come back: confirmed bookings, hand-edited RCAs,
sent status, posted threads. Deleting a "sent" review discards a completed job.

THREE TABLES, NOT ONE. A review is referenced by rca_drafts (a foreign key),
review_metrics and run_jobs. Deleting only `reviews` leaves orphans that no
screen shows and no query cleans up — and an orphaned queued run_job would have
the drain loop trying to process a review that no longer exists.
"""
import argparse
import sys

sys.path.insert(0, ".")


def _where(d) -> str:
    """Which database this process is actually connected to, in words."""
    u = d.engine.url
    if u.get_backend_name().startswith("sqlite"):
        return f"sqlite {u.database or ':memory:'} (a file in THIS container)"
    return f"{u.get_backend_name()} {u.host or '?'}/{u.database or '?'}"


def _boundary(s, d, review_id: str):
    """The cutoff review, or an error naming what to do instead.

    THE REFUSAL NAMES THE DATABASE, and the first version did not. "no review
    X" is the same sentence for an id that is wrong and for an id that is
    simply somewhere else — and this project runs a Development database
    beside a Production one, so "somewhere else" is the likelier of the two.
    Run in the dev repl against a review that only exists in production, the
    honest answer is "I looked in helium/heliumdb", not "it does not exist".
    """
    row = s.query(d.Review).filter(d.Review.id == review_id).first()
    if row is None:
        total = s.query(d.Review).count()
        return None, (
            f"no review {review_id!r} in {_where(d)} — which holds {total} "
            f"review(s).\n"
            f"         This deployment keeps a Development database beside the "
            f"Production one, so the likeliest cause is that you are connected "
            f"to the wrong one rather than that the id is wrong. To act on the "
            f"production data, run this with DATABASE_URL set to the Production "
            f"database:\n"
            f"           DATABASE_URL='<production url>' python3 "
            f"tools/purge_reviews_before.py {review_id}\n"
            f"         Otherwise check the id on the card, or list them with "
            f"GET /api/reviews")
    if row.received_at is None:
        return None, (f"{review_id} has no received_at, so 'before' it cannot be "
                      f"decided. Pick another review as the boundary.")
    return row, ""


def collect(s, d, cutoff_at):
    """Reviews strictly before `cutoff_at`, oldest first.

    STRICTLY before: the boundary review itself is kept. Deleting the row the
    caller named as the edge is the kind of off-by-one nobody notices until the
    review they were protecting is gone.

    THE `isnot(None)` IS BELT-AND-BRACES AND IS SAID TO BE. SQL already drops
    NULLs from a `<` comparison, so removing it changes nothing — mutation
    testing marked exactly that, and it is an equivalent mutant rather than an
    untested branch. It stays because "an undated review is not before
    anything" is the intent, and on the one operation here that cannot be
    undone the intent is worth stating in the query rather than inferred from
    three-valued logic.
    """
    return (s.query(d.Review)
             .filter(d.Review.received_at.isnot(None),
                     d.Review.received_at < cutoff_at)
             .order_by(d.Review.received_at.asc())
             .all())


def purge(s, d, review_ids: list) -> dict:
    """Delete the reviews and their dependent rows. Returns per-table counts.

    Children first: rca_drafts carries a foreign key to reviews, and on a
    database that enforces it the parent delete fails otherwise. Counts are
    returned per table so the caller can SAY what went, rather than reporting a
    single number that hides an orphan left behind.
    """
    if not review_ids:
        return {"reviews": 0, "drafts": 0, "metrics": 0, "jobs": 0}
    out = {}
    out["drafts"] = (s.query(d.RcaDraft)
                      .filter(d.RcaDraft.review_id.in_(review_ids))
                      .delete(synchronize_session=False))
    out["metrics"] = (s.query(d.ReviewMetric)
                       .filter(d.ReviewMetric.review_id.in_(review_ids))
                       .delete(synchronize_session=False))
    out["jobs"] = (s.query(d.RunJob)
                    .filter(d.RunJob.review_id.in_(review_ids))
                    .delete(synchronize_session=False))
    out["reviews"] = (s.query(d.Review)
                       .filter(d.Review.id.in_(review_ids))
                       .delete(synchronize_session=False))
    s.commit()
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("review_id", help="the boundary review; it is KEPT")
    ap.add_argument("--apply", action="store_true",
                    help="actually delete (default is a dry run)")
    a = ap.parse_args()

    import server.db as d
    s = d.SessionLocal()
    try:
        edge, why = _boundary(s, d, a.review_id)
        if edge is None:
            print(f"REFUSING: {why}")
            return 2

        doomed = collect(s, d, edge.received_at)
        print(f"boundary: {edge.id}  {edge.received_at}  ({edge.author})")
        print(f"database: {d.engine.url.host or d.engine.url.database}")
        print(f"{len(doomed)} review(s) received before it:\n")
        sent = 0
        for r in doomed:
            mark = ""
            if (r.status or "") == "sent":
                sent += 1
                mark = "  << SENT — completed work, not recoverable"
            print(f"  {r.received_at}  {r.id}  {(r.author or '')[:24]:24} "
                  f"{r.status}{mark}")
        if sent:
            print(f"\n{sent} of these are SENT. Re-ingesting brings the review "
                  f"back but not the reply, the RCA or the confirmation.")

        if not doomed:
            print("\nnothing to do.")
            return 0
        if not a.apply:
            print(f"\nDRY RUN — nothing was deleted. Re-run with --apply to "
                  f"remove these {len(doomed)} review(s).")
            return 0

        counts = purge(s, d, [r.id for r in doomed])
        print(f"\ndeleted: {counts['reviews']} review(s), {counts['drafts']} "
              f"draft(s), {counts['metrics']} metric(s), {counts['jobs']} "
              f"queued/finished job(s)")
        print("Slack still has the reviews: POST /api/reviews/refresh-slack"
              "?hours=720 re-ingests them if this was a mistake.")
        return 0
    finally:
        s.close()


if __name__ == "__main__":
    raise SystemExit(main())
