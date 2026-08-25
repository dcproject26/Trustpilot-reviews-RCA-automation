#!/usr/bin/env python3
"""Why each unfinished run is unfinished, and what will move it.

    python3 tools/check_runs.py              # what is stuck, and why
    python3 tools/check_runs.py --reap       # end the ones nothing will ever claim
    python3 tools/check_runs.py --close      # ALSO end the ones a worker would claim

READ-ONLY unless --reap or --close is passed.

WHY THIS EXISTS. "The bar will not go away" has at least five causes and they
look identical from the dashboard:

    the drain loop is not running          nothing claims anything
    a row is queued behind a long run      it IS coming, just not yet
    a claim is frozen, retries left        it comes back when the lease lapses
    a claim is frozen, retries SPENT       nothing will ever claim it again
    the batch really is still working      wait

Only the last two look the same on screen and they are opposites: one is a
finished-but-unreported deadlock, the other is work in progress. This prints
the verdict per row from the SAME conditions server/jobs.py claims on, so the
answer is a fact rather than a theory.
"""
import argparse
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

OK, BAD, WARN, INFO = "  OK  ", " FAIL ", " WARN ", " ..   "


def line(state, label, detail=""):
    print(f"[{state}] {label}" + (f"\n         {detail}" if detail else ""))


def _ago(dt, now):
    """'4m ago' / '(never)'. A duration, because the raw timestamp makes a
    reader do the subtraction that decides the answer."""
    if dt is None:
        return "(never)"
    s = int((now - dt).total_seconds())
    sign = "ago" if s >= 0 else "from now"
    s = abs(s)
    if s < 90:
        return f"{s}s {sign}"
    if s < 5400:
        return f"{s // 60}m {sign}"
    return f"{s // 3600}h {sign}"


def verdict(r, now, stall_after_s):
    """(state, sentence) for one unfinished run — the fate of THIS row.

    The conditions are the ones jobs.claim_next and jobs.reap_abandoned use. A
    diagnostic that reimplements what it is checking can agree with itself
    while the code does something else, so where the wording differs from the
    query, the query is what runs.
    """
    att, mx = (r.attempts or 0), (r.max_attempts or 3)
    if r.status == "queued":
        return ("queued", "waiting to be claimed. If nothing claims it within "
                          "a few seconds, no drain loop is running.")
    if r.status != "running":
        return (r.status, "")
    lease = r.lease_expires_at
    moved = r.updated_at or r.created_at
    since = int((now - moved).total_seconds()) if moved else None

    if lease is not None and lease > now:
        if since is not None and since < stall_after_s:
            return ("running", f"a worker is on it — last moved {since}s ago.")
        return ("claimed-quiet",
                f"claimed, lease still live, but nothing has written progress "
                f"for {_ago(moved, now)}. Either a very slow stage, or the "
                f"instance died and the lease has not lapsed yet — it becomes "
                f"reclaimable at {lease:%H:%M:%S} UTC.")
    if att < mx:
        return ("reclaimable",
                f"the lease lapsed {_ago(lease, now)} and it has {mx - att} "
                f"attempt(s) left, so the next drain tick claims it. If it "
                f"does not, nothing is draining.")
    return ("STRANDED",
            f"the lease lapsed {_ago(lease, now)} and all {mx} attempts are "
            f"spent. claim_next takes queued rows and reclaimable ones — this "
            f"is neither, so NOTHING will ever look at it again. It pins "
            f"`running` true, which is what keeps the bulk bar on screen. "
            f"--reap ends it as failed.")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--reap", action="store_true",
                    help="mark STRANDED runs failed (what the drain loop does)")
    ap.add_argument("--close", action="store_true",
                    help="ALSO end queued/reclaimable runs — use when you want "
                         "the batch over regardless")
    args = ap.parse_args()

    try:
        from server.db import init_db
        init_db()
    except Exception as e:
        line(BAD, f"this database could not be opened: {type(e).__name__}: "
                  f"{str(e).splitlines()[0][:200]}",
             "Run this where the server runs, with its DATABASE_URL.")
        return 2

    from server import jobs
    from server.db import SessionLocal, RunJob
    from server.tiers import STALL_AFTER_S

    now = datetime.utcnow()
    s = SessionLocal()
    try:
        counts = {}
        for r in s.query(RunJob).all():
            counts[r.status] = counts.get(r.status, 0) + 1
        print(f"\n═══ run_jobs ═══")
        line(OK if counts else WARN, str(counts or "empty"),
             "" if counts else "No job rows at all — nothing has ever been "
                               "queued through the durable path on this DB.")

        live = (s.query(RunJob)
                 .filter(RunJob.status.in_(("queued", "running")))
                 .order_by(RunJob.created_at).all())
        print(f"\n═══ unfinished runs ({len(live)}) ═══")
        if not live:
            line(OK, "nothing is queued or running",
                 "If a progress bar is still on screen, reload the page — the "
                 "database says this batch is over.")
        tally = {}
        for r in live:
            state, why = verdict(r, now, STALL_AFTER_S)
            tally[state] = tally.get(state, 0) + 1
            mark = {"running": OK, "queued": INFO,
                    "reclaimable": WARN, "claimed-quiet": WARN}.get(state, BAD)
            line(mark, f"{r.review_id}  [{state}]  {r.reason}",
                 f"attempts {r.attempts or 0}/{r.max_attempts or 3} · "
                 f"claimed by {r.claimed_by or '(nobody)'} · "
                 f"last moved {_ago(r.updated_at or r.created_at, now)}"
                 + (f"\n         {why}" if why else ""))

        print(f"\n═══ what this means ═══")
        if tally.get("STRANDED"):
            line(BAD, f"{tally['STRANDED']} run(s) are stranded",
                 "Nothing will ever claim these. The drain loop reaps them on "
                 "its next tick — if they are still here a minute after a "
                 "restart, the drain loop is NOT running (it starts in "
                 "server/main.py's lifespan). `--reap` ends them by hand.")
        if tally.get("queued") and not tally.get("running"):
            line(BAD, "queued work and NOTHING claimed",
                 "No row is `running` at all, so no drain loop is claiming. "
                 "Check the app actually booted.")
        if tally.get("running"):
            line(OK, "a worker is draining",
                 "Queued rows are waiting their turn, not abandoned: runs go "
                 "one at a time and each costs its full pipeline latency.")

        bs = jobs.batch_status()
        print(f"\n═══ the bulk bar reads ═══")
        line(INFO, f"{bs['done']}/{bs['total']} done · {bs['failed']} failed · "
                   f"running={bs['running']} · {bs.get('current_state') or '—'}",
             (f"batch {bs['batch']}" if bs.get("batch") else "no bulk batch has run")
             + (f" · {bs['stranded']} stranded" if bs.get("stranded") else "")
             + ("\n         `running` is what keeps the bar on screen."
                if bs.get("running") else
                "\n         The bar should be gone. If it is not, reload the page."))
    finally:
        s.close()

    if args.reap or args.close:
        n = jobs.reap_abandoned()
        print()
        line(OK, f"reaped {n} stranded run(s)",
             "They are marked failed, not done — a run that never happened "
             "must not be reported as one that did.")
        if args.close:
            m = jobs.cancel_batch()
            line(OK, f"cancelled {m} queued run(s) in the newest batch",
                 "A run already in progress is left alone: killing its row "
                 "mid-pipeline would leave a half-written draft.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
