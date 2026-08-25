#!/usr/bin/env python3
"""Why each Zendesk ticket is in — or out of — a booking's timeline, and
whether re-runs are actually running.

    python3 tools/check_timeline.py 33543686          # by booking id
    python3 tools/check_timeline.py tp_1787370328_197709   # by review id
    python3 tools/check_timeline.py 33543686 --rerun  # also queue a re-run

READ-ONLY unless --rerun is passed.

WHY THIS EXISTS. "The timeline shows unrelated tickets" and "the timeline is
empty" are both reported the same way — by looking at a card — and they have
about six different causes between them: a ticket found by the free-text route
that belongs to someone else, a prior trip, a rate limit that emptied the
lookup, a booking with no date so the date filter never ran, a run that never
happened. This prints the decision for every ticket, so the answer is a fact
rather than a theory.

Every judgement it prints is made by the REAL functions the pipeline uses, not
a copy of their logic — a diagnostic that reimplements what it is checking can
agree with itself while the pipeline does something else.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD, WARN, INFO = "  OK  ", " FAIL ", " WARN ", " ..   "


def line(state, label, detail=""):
    print(f"[{state}] {label}" + (f"\n         {detail}" if detail else ""))


def resolve(target: str):
    """(booking_id, booked_on, review_id) from a booking id or a review id."""
    from server.db import SessionLocal, Review, RcaDraft
    from server.services.zendesk import _booking_date
    s = SessionLocal()
    try:
        if target.startswith("tp_"):
            r = s.query(Review).filter(Review.id == target).first()
            if not r:
                return None, "", target
            d = s.query(RcaDraft).filter(RcaDraft.review_id == target).first()
            bk = (d.booking if d else None) or {}
            return (bk.get("id") or r.reference_number or ""), _booking_date(bk), target
        # a booking id: find the review that carries it, for its booked-on date
        d = (s.query(RcaDraft)
              .filter(RcaDraft.booking.isnot(None)).all())
        for row in d:
            if str((row.booking or {}).get("id") or "") == target:
                return target, _booking_date(row.booking or {}), row.review_id
        return target, "", ""
    finally:
        s.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a booking id (33543686) or a review id (tp_...)")
    ap.add_argument("--rerun", action="store_true",
                    help="queue a durable re-run for the review and report it")
    args = ap.parse_args()

    from server.config import is_live
    from server.services import zendesk as Z

    bid, booked_on, review_id = resolve(args.target)
    print(f"\n═══ target {args.target} ═══")
    line(OK if bid else BAD, f"booking id: {bid or '(none)'}",
         "" if bid else "Nothing to search Zendesk with — every ticket lookup "
                        "is keyed on a booking id, so the timeline would be "
                        "empty for that reason alone.")
    line(OK if booked_on else WARN, f"booked on: {booked_on or '(unknown)'}",
         "" if booked_on else "With no booking date the prior-trip filter "
                              "CANNOT RUN — it says so rather than silently "
                              "keeping everything.")
    if review_id:
        line(INFO, f"review: {review_id}")
    if not bid:
        return 2

    if not is_live("zendesk"):
        line(BAD, "Zendesk is not live on this server",
             "Run this where the server runs; it reads the server's own config.")
        return 2

    # ── 1. what the search found, by route ──────────────────────────────────
    print(f"\n═══ 1. ticket search ═══")
    z = Z._get_client()
    try:
        tickets, tally = Z.collect_tickets(bid, lambda q: Z._search_with_retry(z, q))
    except Z.ZendeskRateLimited as e:
        line(BAD, "RATE LIMITED before the search finished", str(e))
        return 1
    line(OK, f"{len(tickets)} ticket(s) found", str(tally))
    if tally.get("free_text"):
        line(INFO, f"{tally['free_text']} came from the FREE-TEXT route",
             "That route matches any ticket whose TEXT contains the booking id "
             "— including support-history digests about other bookings. It is "
             "the usual source of a stranger's ticket in this timeline.")

    # ── 2. the filter decision, per ticket ──────────────────────────────────
    print(f"\n═══ 2. per-ticket verdict ═══")
    cutoff, cutoff_reason = Z._booking_cutoff(booked_on)
    if cutoff_reason:
        line(WARN, "the date filter did not run", cutoff_reason)

    kept, dropped = [], []
    for t in tickets:
        tid = str(getattr(t, "id", ""))
        subj = (getattr(t, "subject", "") or "")[:52]
        field = Z.booking_id_from_ticket(t) or ""
        names = Z.other_booking_named(t, bid)

        if names:
            why = (f"its booking field says {names}" if field
                   else f"its SUBJECT names BID-{names}")
            dropped.append((tid, f"another booking ({names})", why, subj))
            continue

        last = None
        if cutoff is not None:
            try:
                cs = Z.zd_call(lambda tt=t: list(z.tickets.comments(ticket=tt.id)),
                               f"comments ZD-{tid}")
                last = max((Z._sort_key(getattr(c, "created_at", None))
                            for c in cs), default=None)
            except Z.ZendeskRateLimited as e:
                line(BAD, f"RATE LIMITED reading ZD-{tid}", str(e))
                return 1
            except Exception as e:
                line(WARN, f"could not read ZD-{tid}'s comments: {e}")
        if last is not None and Z._is_prior_trip(last, cutoff):
            dropped.append((tid, "prior trip",
                            f"newest activity {Z._to_iso(last)} predates the booking",
                            subj))
            continue
        kept.append((tid, field or "(empty)", Z._to_iso(last) if last else "?", subj))

    print(f"\n  KEPT — these become the timeline ({len(kept)}):")
    for tid, field, last, subj in kept:
        print(f"    ZD-{tid:<11} field={field:<11} newest={last[:19]:<19} {subj!r}")
    print(f"\n  DROPPED ({len(dropped)}):")
    if not dropped:
        print("    (none)")
    for tid, kind, why, subj in dropped:
        print(f"    ZD-{tid:<11} {kind:<22} {subj!r}\n         └─ {why}")

    # ── 3. what the pipeline would actually build ───────────────────────────
    print(f"\n═══ 3. the timeline the pipeline builds ═══")
    try:
        raw, _extracted, meta = Z._get_timeline_sync(z, bid, booked_on)
    except Z.ZendeskRateLimited as e:
        line(BAD, "RATE LIMITED building the timeline", str(e))
        return 1
    line(OK if raw else WARN, f"{len(raw)} event(s) across {len(meta['ticket_ids'])} ticket(s)",
         "" if raw else "No events. With tickets kept above, this means their "
                        "comments were unreadable — look for a WARN line.")
    if meta.get("other_booking_excluded"):
        line(OK, f"{len(meta['other_booking_excluded'])} kept off as another booking's",
             str(meta["other_booking_excluded"]))
    if meta.get("prior_trip_excluded"):
        line(OK, f"{len(meta['prior_trip_excluded'])} kept off as a prior trip",
             str(meta["prior_trip_excluded"]))
    if meta.get("prior_trip_reason"):
        line(WARN, "prior-trip filter did not run", meta["prior_trip_reason"])

    # ── 4. re-runs ──────────────────────────────────────────────────────────
    print(f"\n═══ 4. re-run health ═══")
    from server import jobs
    from server.db import SessionLocal, RunJob
    s = SessionLocal()
    try:
        recent = (s.query(RunJob).order_by(RunJob.created_at.desc()).limit(8).all())
        counts = {}
        for r in s.query(RunJob).all():
            counts[r.status] = counts.get(r.status, 0) + 1
        line(OK if counts else WARN, f"run_jobs: {counts or 'empty'}",
             "" if counts else "No job rows at all — nothing has ever been "
                               "queued through the durable path on this DB.")
        for r in recent:
            print(f"    {(r.created_at or '')!s:19} {r.status:<8} {r.reason:<24} {r.review_id}")
        stuck = [r for r in recent if r.status == "queued"]
        if stuck:
            line(WARN, f"{len(stuck)} job(s) queued and not started",
                 "If this does not fall to 0 within a minute or two, the drain "
                 "loop is not running — it starts in server/main.py's lifespan, "
                 "so check the app actually booted.")
    finally:
        s.close()

    bs = jobs.batch_status()
    line(INFO, f"bulk batch: {bs['done']}/{bs['total']} done, "
               f"{bs['failed']} failed, running={bs['running']}",
         f"batch {bs['batch']}" if bs.get("batch") else "no bulk batch has run")

    if args.rerun and review_id:
        jid = jobs.enqueue(review_id, "check-timeline", True)
        line(OK, f"queued a durable re-run: job {jid}",
             "Re-run this script in a minute; the job should read done and the "
             "timeline above should be rebuilt.")
    elif args.rerun:
        line(WARN, "cannot queue a re-run", "no review id resolved for that target")

    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
