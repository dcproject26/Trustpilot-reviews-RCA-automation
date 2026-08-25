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


def _migrate_first():
    """Bring the schema up to the models before querying them.

    Same reason tools/check_slack_ingestion.py does it: a diagnostic is what
    someone runs BEFORE restarting anything, so it cannot assume the server has
    already created the tables. Without this the first query dies on "no such
    table: rca_drafts" — a raw SQLAlchemy traceback, which reads as a broken
    tool rather than an empty database. init_db() is idempotent.

    Returns "" when the schema is ready, or the sentence to report with. A
    database this tool cannot even open is a FINDING — it is most of the answer
    when someone asks why a timeline is empty — so it comes back as one rather
    than as a stack trace out of a diagnostic.
    """
    try:
        from server.db import init_db
        init_db()
        return ""
    except Exception as e:
        return (f"this database could not be opened: {type(e).__name__}: "
                f"{str(e).splitlines()[0][:200]}")


def resolve(target: str):
    """(booking_id, booked_on, review_id) from a booking id or a review id.

    Never raises. A database this tool cannot read is a FINDING — it is most of
    the answer when someone asks why a timeline is empty — so it is returned as
    one rather than thrown as a stack trace.
    """
    from server.db import SessionLocal, Review, RcaDraft
    from server.services.zendesk import _booking_date
    s = SessionLocal()
    try:
        return _resolve(s, target, Review, RcaDraft, _booking_date)
    except Exception as e:
        print(f"[{BAD}] this database could not be read: "
              f"{type(e).__name__}: {str(e).splitlines()[0][:200]}")
        print("         That is most of the answer: with no readable database "
              "there is no booking to look up. Run this where the server runs.")
        return None, "", ""
    finally:
        s.close()


def _resolve(s, target, Review, RcaDraft, _booking_date):
    if True:
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


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("target", help="a booking id (33543686) or a review id (tp_...)")
    ap.add_argument("--rerun", action="store_true",
                    help="queue a durable re-run for the review and report it")
    args = ap.parse_args()

    # KEEP UNRELATED WARNINGS OUT OF A DIAGNOSTIC. Importing the pipeline pulls
    # in the Sheets connector, which logs "Google Sheets connector not
    # available" twice per run. Printed between "═══ 1. ticket search ═══" and
    # its result, it reads as the ticket search failing — a report whose job is
    # to tell a real fault from a healthy empty must not manufacture a false
    # one. Sheets export is not on any path this tool exercises. Silenced HERE
    # only; the server still logs it.
    import logging as _lg
    _lg.getLogger("server.services.sheets_connector").setLevel(_lg.ERROR)

    from server.config import is_live
    from server.services import zendesk as Z

    _bad_db = _migrate_first()
    if _bad_db:
        print(f"\n═══ target {args.target} ═══")
        line(BAD, _bad_db,
             "With no readable database there is no booking to look up. Run "
             "this where the server runs, with its DATABASE_URL.")
        return 2
    bid, booked_on, review_id = resolve(args.target)
    print(f"\n═══ target {args.target} ═══")
    line(OK if bid else BAD, f"booking id: {bid or '(none)'}",
         "" if bid else "Nothing to search Zendesk with — every ticket lookup "
                        "is keyed on a booking id, so the timeline would be "
                        "empty for that reason alone.")
    # SHOW THE DATE, NOT THE STORED STRING. The warehouse hands back booked-on
    # as epoch seconds in scientific notation ("1.787097364E9"). _sort_key
    # parses it, so the filter runs — but printing the raw value made the
    # headline field of this report look like garbage, which teaches a reader
    # to distrust every section under it. Both are shown: the date because it
    # is the fact, the raw string because it is what a mis-parse would have to
    # be diagnosed from.
    _when, _why = Z._booking_cutoff(booked_on)
    if booked_on and _when is not None:
        line(OK, f"booked on: {Z._to_iso(_when)[:19]}  (stored as {booked_on!r})")
    else:
        line(WARN, f"booked on: {booked_on or '(unknown)'}",
             (_why or "no booking date") + " — the prior-trip filter CANNOT "
             "RUN, and says so rather than silently keeping everything.")
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
    # The one that is our fault rather than a decision. Section 3's empty-state
    # used to tell the reader to "look for a WARN line" above — this is that
    # line, promoted to a finding, because a conversation the search found and
    # the fetch could not open is missing evidence and the RCA is written
    # without it.
    if meta.get("unreadable_tickets"):
        _ur = meta["unreadable_tickets"]
        line(BAD, f"{len(_ur)} ticket(s) FOUND BUT UNREADABLE",
             "; ".join(f"ZD-{e.get('ticket_id')} — {e.get('error')}" for e in _ur)
             + ". Nothing from these is in the timeline. An 'not on record' "
               "finding about this booking may be one of these conversations.")

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
        # IS ANYTHING DRAINING? ANSWER IT, DO NOT ASK THE READER TO.
        # This used to print "if this does not fall to 0 in a minute or two,
        # the drain loop is not running" — handing back the one question the
        # rows can already answer. A claimed row's updated_at moves at every
        # pipeline stage (jobs.note_progress), so a worker that is alive is
        # visible in the table and one that died mid-run is too: its row keeps
        # status `running` on a lease that has not lapsed, frozen at the
        # instant it was claimed. That is what a restart leaves behind, and a
        # `git pull` on the repl is a restart.
        stuck = [r for r in recent if r.status == "queued"]
        allrun = s.query(RunJob).filter(RunJob.status == "running").all()
        moving = [(r, jobs.worker_liveness(r)[1]) for r in allrun
                  if jobs.worker_liveness(r)[0] == "running"]
        frozen = [(r, jobs.worker_liveness(r)[1]) for r in allrun
                  if jobs.worker_liveness(r)[0] == "stalled"]
        if moving:
            r, since = min(moving, key=lambda x: x[1])
            line(OK, f"a worker IS draining — {r.review_id} moved {since}s ago",
                 f"claimed by {r.claimed_by or '(unrecorded)'}. Queued jobs are "
                 f"waiting their turn, not abandoned: runs go one at a time.")
        elif allrun:
            line(BAD, f"{len(allrun)} job(s) claimed, NONE of them moving",
                 "Every running row is frozen — the instance that claimed it is "
                 "gone. They are not lost: each becomes reclaimable when its "
                 "14-minute lease lapses. If this repeats, the drain loop is "
                 "not starting — it lives in server/main.py's lifespan.")
        elif stuck:
            line(BAD, f"{len(stuck)} job(s) queued and NOTHING has claimed one",
                 "No row is in `running` at all, so no drain loop is claiming. "
                 "It starts in server/main.py's lifespan — check the app booted.")
        if frozen and moving:
            line(WARN, f"{len(frozen)} claimed row(s) frozen alongside a live one",
                 "Runs are serial, so more than one `running` row means an "
                 "earlier claim outlived its worker: "
                 + ", ".join(f"{r.review_id} ({since}s)" for r, since in frozen))
    finally:
        s.close()

    bs = jobs.batch_status()
    line(INFO, f"bulk batch: {bs['done']}/{bs['total']} done, "
               f"{bs['failed']} failed, running={bs['running']}",
         (f"batch {bs['batch']} — {bs['current_state']}"
          + (f" on {bs['current']}" if bs.get("current") else "")
          + (f", {bs['stalled']} stalled" if bs.get("stalled") else ""))
         if bs.get("batch") else "no bulk batch has run")

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
