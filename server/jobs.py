"""Durable pipeline runs — a job row any instance can claim, and a worker loop
that drains it.

WHY THIS EXISTS, AND WHAT IT CANNOT DO. Every run path was
`background_tasks.add_task(run_batch_sync, ...)`: a task run AFTER the response,
in that request's process. On an autoscale deployment the container is reclaimed
once the response is sent, so the task frequently never ran — measured, 0 of 9
re-runs moved a single field. A row in `run_jobs` outlives the request; the
drain loop on any live instance claims it and runs it, and a claim a reclaimed
container never finished is reclaimed once its lease lapses.

The honest limit, stated so nobody reads a guarantee that is not here: on
autoscale an instance exists only while it is serving traffic. This makes a run
SURVIVABLE and RESUMABLE — it is not lost, and it is retried until an instance
lives long enough to finish it — but it cannot run on a container that does not
exist. A deployment with steady traffic (the dashboard polls, which keeps a
container warm) drains promptly; a fully idle one drains when the next request
wakes an instance. The drain runs in a loop started from the lifespan
(server/main.py), and each run still costs its full pipeline latency there.

The claim is a compare-and-swap — a conditional UPDATE whose row count tells the
worker whether it won — so it needs neither FOR UPDATE nor SKIP LOCKED and is
correct on both Postgres and SQLite.
"""
import logging
import uuid
from datetime import datetime, timedelta

log = logging.getLogger(__name__)

# The run budget is 12 minutes (pipeline.RUN_TIMEOUT_S); the lease is that plus
# a margin, so a live run is never reclaimed out from under itself, and a dead
# one becomes reclaimable a couple of minutes after it would have finished.
LEASE_S = 14 * 60

# review_id -> job_id for jobs running IN THIS process, so _progress can find
# the job to write progress onto and renew its lease. In-process only; a job
# running on another instance is not in here, which is correct — this instance
# is not the one making its progress.
_ACTIVE: dict = {}


def _db():
    import server.db as d
    return d


def enqueue(review_id: str, reason: str = "re-run",
            force_candidates: bool = False) -> str:
    """Queue a durable run for `review_id`, returning the job id.

    Deduped: a review with a job already queued or running does not get a
    second — clicking Re-run twice, or two paths scheduling the same review,
    must not run it twice at once. The existing job's id is returned instead.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        existing = (s.query(d.RunJob)
                     .filter(d.RunJob.review_id == review_id,
                             d.RunJob.status.in_(("queued", "running")))
                     .first())
        if existing is not None:
            return existing.id
        jid = uuid.uuid4().hex
        s.add(d.RunJob(id=jid, review_id=review_id, reason=reason,
                       force_candidates=bool(force_candidates), status="queued",
                       attempts=0, created_at=datetime.utcnow(),
                       updated_at=datetime.utcnow()))
        s.commit()
        return jid
    finally:
        s.close()


def claim_next(worker_id: str, lease_s: int = LEASE_S):
    """Claim the oldest claimable job, atomically. Returns a detached dict of
    the claimed job's fields, or None.

    Claimable = queued, or running with a lapsed lease and attempts left. The
    claim is a CAS: SELECT a candidate, then UPDATE it only while it is still in
    the state we saw. The row count says whether we won; a loser tries the next
    candidate. Every claim spends one attempt, so a job that keeps being claimed
    and lost to a dying container is bounded by max_attempts, not retried for
    ever.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        for _ in range(25):
            now = datetime.utcnow()
            job = (s.query(d.RunJob)
                    .filter(d.RunJob.status == "queued")
                    .order_by(d.RunJob.created_at).first())
            reclaim = False
            if job is None:
                job = (s.query(d.RunJob)
                        .filter(d.RunJob.status == "running",
                                d.RunJob.lease_expires_at < now,
                                d.RunJob.attempts < d.RunJob.max_attempts)
                        .order_by(d.RunJob.created_at).first())
                reclaim = job is not None
            if job is None:
                return None
            jid = job.id
            q = s.query(d.RunJob).filter(d.RunJob.id == jid)
            if reclaim:
                q = q.filter(d.RunJob.status == "running",
                             d.RunJob.lease_expires_at < now)
            else:
                q = q.filter(d.RunJob.status == "queued")
            won = q.update({d.RunJob.status: "running",
                            d.RunJob.claimed_by: worker_id,
                            d.RunJob.lease_expires_at: now + timedelta(seconds=lease_s),
                            d.RunJob.attempts: d.RunJob.attempts + 1,
                            d.RunJob.updated_at: now},
                           synchronize_session=False)
            s.commit()
            if won == 1:
                c = s.query(d.RunJob).get(jid)
                return {"id": c.id, "review_id": c.review_id, "reason": c.reason,
                        "force_candidates": bool(c.force_candidates),
                        "attempts": c.attempts, "max_attempts": c.max_attempts}
            # lost the race — someone else claimed it; try the next candidate
        return None
    finally:
        s.close()


def reap_abandoned() -> int:
    """Mark stranded `running` jobs dead. Returns how many were reaped.

    THE DEADLOCK THIS CLEARS, and it is a hole in the state machine rather than
    a slow run. claim_next takes a queued job, or a `running` job whose lease
    has lapsed AND that has attempts left. A row that is `running`, lapsed, and
    OUT of attempts matches neither:

        queued            no  — it is `running`
        reclaimable       no  — attempts >= max_attempts
        done / dead       no  — nothing ever moved it there

    It happens the ordinary way: the container dies mid-run, so `fail()` never
    runs to spend the attempt properly, three times over. The row then sits in
    `running` for ever. batch_status counts queued+running as live, so
    `running` stays true, and the bulk bar — which hides itself only when the
    batch is not running — never goes away. That is the "0/1 and it will not
    clear" report, and no amount of waiting fixes it because nothing is left
    that would ever look at the row again.

    Reaping is the honest end: we tried it max_attempts times and every attempt
    died with the container. It becomes `dead`, which the bar counts as FAILED
    rather than done — a run that never completed must not be reported as one
    that did.

    A LIVE LEASE IS NEVER TOUCHED. A long run renews its lease at every stage
    (note_progress), so only a row nobody has renewed for its whole lease can
    be reaped — killing a live run's row would leave a half-written draft and
    put a second run over the same review.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        now = datetime.utcnow()
        n = (s.query(d.RunJob)
              .filter(d.RunJob.status == "running",
                      # NO EXPLICIT NULL GUARD, and that is deliberate. finish()
                      # and fail() null the lease, so a NULL here is a row
                      # mid-transition and must never be reaped. SQL already
                      # delivers that: `NULL < now` is NULL, not true, so those
                      # rows do not match. An `isnot(None)` alongside it read as
                      # a defence and could not change a single outcome —
                      # mutation-tested, it survived because it is equivalent
                      # code. If this filter is ever moved into Python, `None <
                      # datetime` RAISES and the equivalence is gone: the
                      # behaviour is pinned by
                      # test_a_row_with_no_lease_at_all_is_left_alone.
                      d.RunJob.lease_expires_at < now,
                      d.RunJob.attempts >= d.RunJob.max_attempts)
              .update({d.RunJob.status: "dead",
                       d.RunJob.last_error:
                           "the run was claimed and never finished; every "
                           "attempt was lost with the instance running it",
                       d.RunJob.lease_expires_at: None,
                       d.RunJob.updated_at: now},
                      synchronize_session=False))
        s.commit()
        if n:
            log.warning(f"[jobs] reaped {n} abandoned run(s) — claimed, out of "
                        f"attempts, and never finished")
        return int(n or 0)
    finally:
        s.close()


def note_progress(review_id: str, step: int, total: int, stage: str) -> None:
    """Write a running job's progress to its row and renew its lease.

    Called from pipeline._progress. Only touches a job this process is actively
    running (in _ACTIVE), so it never fights another instance for the row, and
    the lease renewal is what keeps a genuinely-long-but-alive run from being
    reclaimed at the 14-minute mark. Best-effort: a progress write must never
    take down the run it is describing.
    """
    jid = _ACTIVE.get(review_id)
    if not jid:
        return
    d = _db()
    s = d.SessionLocal()
    try:
        now = datetime.utcnow()
        s.query(d.RunJob).filter(d.RunJob.id == jid).update(
            {d.RunJob.progress: {"step": step, "total": total, "stage": stage,
                                 "updated_at": now.isoformat()},
             d.RunJob.lease_expires_at: now + timedelta(seconds=LEASE_S),
             d.RunJob.updated_at: now},
            synchronize_session=False)
        s.commit()
    except Exception as e:
        log.debug(f"[jobs] progress write for {jid} failed (non-fatal): {e}")
    finally:
        s.close()


def finish(job_id: str) -> None:
    """Mark a job done."""
    d = _db()
    s = d.SessionLocal()
    try:
        s.query(d.RunJob).filter(d.RunJob.id == job_id).update(
            {d.RunJob.status: "done", d.RunJob.lease_expires_at: None,
             d.RunJob.updated_at: datetime.utcnow()},
            synchronize_session=False)
        s.commit()
    finally:
        s.close()


def fail(job_id: str, reason: str) -> str:
    """Record a failed run. Re-queue it if attempts remain, else mark it dead.

    Returns the resulting status ("queued" or "dead"). A job that fails
    identically for ever is a new way to hide a bug, so a bounded count of
    attempts (spent at claim time) turns into `dead` with the reason kept on the
    row — a state the list can show, not a silent retry loop.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        job = s.query(d.RunJob).get(job_id)
        if job is None:
            return "gone"
        reason = (reason or "")[:4000]
        if (job.attempts or 0) >= (job.max_attempts or 3):
            status = "dead"
            s.query(d.RunJob).filter(d.RunJob.id == job_id).update(
                {d.RunJob.status: "dead", d.RunJob.last_error: reason,
                 d.RunJob.lease_expires_at: None, d.RunJob.updated_at: datetime.utcnow()},
                synchronize_session=False)
        else:
            status = "queued"
            s.query(d.RunJob).filter(d.RunJob.id == job_id).update(
                {d.RunJob.status: "queued", d.RunJob.last_error: reason,
                 d.RunJob.lease_expires_at: None, d.RunJob.claimed_by: None,
                 d.RunJob.updated_at: datetime.utcnow()},
                synchronize_session=False)
        s.commit()
        return status
    finally:
        s.close()


def job_states() -> dict:
    """{review_id: "running" | "queued"} for every active job, per the DB.

    The cross-instance answer to "is a run booked or in progress for this
    review?". processing_state reads this so a run on ANOTHER instance is
    reported as running (not the dead run it looks like from a process that is
    not the one running it), and a freshly-enqueued review reads as queued
    rather than as a blank card. A `running` job whose lease has lapsed is NOT
    reported here — it looks alive but its instance is gone, so it should read
    as a dead run and be reclaimable, not as "still running".

    running wins over queued if somehow both exist for a review (it should not,
    given enqueue dedupes).
    """
    d = _db()
    s = d.SessionLocal()
    try:
        now = datetime.utcnow()
        out: dict = {}
        for rid, in (s.query(d.RunJob.review_id)
                      .filter(d.RunJob.status == "queued").all()):
            out[rid] = "queued"
        for rid, in (s.query(d.RunJob.review_id)
                      .filter(d.RunJob.status == "running",
                              d.RunJob.lease_expires_at > now).all()):
            out[rid] = "running"
        return out
    finally:
        s.close()


# HOW LONG A CLICK WAITS BEFORE ANYTHING LOOKS AT IT. This was 15 seconds, and
# that is the whole of the "it takes a long time to start" a re-run shows: the
# job row is written the instant the button is pressed, and then nothing reads
# the table until the next tick. Three seconds is a single indexed SELECT on a
# table with tens of rows — the cost is nothing next to a button that appears
# not to have worked.
#
# It does NOT make a run faster. Runs are serial and each costs its full
# pipeline latency, so a queue of nine still drains one at a time; this only
# shortens the dead air before the FIRST one moves.
IDLE_SLEEP_S = 3.0


async def run_drain_loop(runner, worker_id: str, idle_sleep_s: float = IDLE_SLEEP_S,
                         should_stop=None) -> None:
    """Claim and run jobs, one at a time, for as long as this instance lives.

    `runner(job_dict)` is awaited to execute one job — injected so this module
    does not import the pipeline. Runs are serial (the project's own model:
    "runs go one at a time"), and the loop exits cleanly when `should_stop()`
    returns True, so the lifespan can cancel it on shutdown.
    """
    import asyncio
    log.info(f"[jobs] drain loop started ({worker_id})")
    while not (should_stop and should_stop()):
        job = None
        try:
            # BEFORE claiming, not after: a stranded row is invisible to
            # claim_next by construction (see reap_abandoned), so nothing else
            # in this loop would ever look at it again. Best-effort — a reap
            # that fails must not stop the loop from draining real work.
            try:
                reap_abandoned()
            except Exception as e:
                log.warning(f"[jobs] reap failed (non-fatal): {e}")
            job = claim_next(worker_id)
        except Exception as e:
            log.warning(f"[jobs] claim failed (non-fatal): {e}")
        if job is None:
            await asyncio.sleep(idle_sleep_s)
            continue
        rid = job["review_id"]
        _ACTIVE[rid] = job["id"]
        try:
            await runner(job)
            finish(job["id"])
        except Exception as e:
            log.exception(f"[jobs] run for {rid} failed: {e}")
            outcome = fail(job["id"], f"{type(e).__name__}: {e}")
            log.info(f"[jobs] {rid} -> {outcome} (attempt {job['attempts']}/{job['max_attempts']})")
        finally:
            _ACTIVE.pop(rid, None)


# ── batches, for the bulk re-run ────────────────────────────────────────────
# "Fix incomplete" was the ONE re-run path still using a fire-and-forget
# BackgroundTask with in-process progress state, while every other path
# (per-review re-run, candidate-confirmed, bid-set-by-hand, slack-refresh)
# already used the rows above. On autoscale that is two separate failures:
#
#   * the work may never run. A BackgroundTask executes AFTER the response, and
#     the container can be reclaimed the moment the response is sent — the same
#     defect measured at "0 of 9 re-runs executed" that these job rows exist to
#     fix.
#   * the progress cannot be seen. The counters lived in a module-level dict,
#     so a poll landing on another instance found a run that, as far as that
#     process knew, had never started.
#
# A batch is just a shared `reason` on ordinary job rows, so the drain loop
# needs no special case and progress is a query rather than a memory.

BULK_REASON_PREFIX = "fix-incomplete"


def start_batch(review_ids, reason_prefix: str = BULK_REASON_PREFIX) -> dict:
    """Queue one durable job per review under a single batch reason.

    Returns {"batch", "reason", "queued": [...], "already_booked": [...]}.

    ALREADY_BOOKED IS RETURNED, NOT SWALLOWED. `enqueue` dedupes a review that
    already has a job queued or running, which is right — a bulk run must not
    start a second run over a review someone is already re-running. But then
    "60 reviews queued" and "60 reviews looked at, 12 of which were already
    booked and are not part of this batch" are the same number, and the second
    is what happened. The caller can say so.
    """
    import uuid as _uuid
    d = _db()
    s = d.SessionLocal()
    batch = _uuid.uuid4().hex[:8]
    reason = f"{reason_prefix}:{batch}"
    queued, already = [], []
    try:
        for rid in review_ids:
            existing = (s.query(d.RunJob)
                         .filter(d.RunJob.review_id == rid,
                                 d.RunJob.status.in_(("queued", "running")))
                         .first())
            if existing is not None:
                already.append(rid)
                continue
            now = datetime.utcnow()
            s.add(d.RunJob(id=_uuid.uuid4().hex, review_id=rid, reason=reason,
                           force_candidates=False, status="queued", attempts=0,
                           created_at=now, updated_at=now))
            queued.append(rid)
        s.commit()
    finally:
        s.close()
    return {"batch": batch, "reason": reason,
            "queued": queued, "already_booked": already}


def worker_liveness(row, now=None) -> tuple:
    """(state, seconds_since_it_moved) for one `running` job row.

    "running" | "stalled". A row goes `running` the moment it is CLAIMED, and
    its updated_at moves only while a worker writes progress onto it
    (note_progress, called from pipeline._progress at every stage). So the row
    of a worker that died mid-run keeps status `running` and a lease that has
    not lapsed for another fourteen minutes, while its updated_at is frozen at
    the instant it was claimed.

    That is not a corner case — it is what a restart does, and a `git pull` on
    the repl is a restart. It left two rows `running` in one serial drain, and
    the bulk bar picked the dead one to display.

    The lease is the RECLAIM clock and this is the DISPLAY clock, deliberately
    different: reclaiming early would start a second run over a review that is
    merely slow, but calling a frozen row "running" on screen tells a reader a
    worker is on it when none is. STALL_AFTER_S is the same threshold
    tiers.liveness applies to the in-process entry, so a run does not read as
    alive in one place and stalled in the other.
    """
    from server.tiers import STALL_AFTER_S
    now = now or datetime.utcnow()
    last = row.updated_at or row.created_at or now
    since = max(0, int((now - last).total_seconds()))
    return ("stalled" if since >= STALL_AFTER_S else "running"), since


def batch_status(reason_prefix: str = BULK_REASON_PREFIX) -> dict:
    """Progress of the most recent batch, read from the job rows.

    Survives the request, the container and the instance, because it is a
    query rather than a process's memory — which is the whole point of moving
    the bulk run onto these rows.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        newest = (s.query(d.RunJob)
                   .filter(d.RunJob.reason.like(f"{reason_prefix}:%"))
                   .order_by(d.RunJob.created_at.desc()).first())
        if newest is None:
            return {"running": False, "total": 0, "done": 0, "failed": 0,
                    "remaining": 0, "current": "", "current_state": "",
                    "stalled": 0, "stranded": 0, "scope": "", "batch": "",
                    "started_at": None, "finished_at": None, "eta_s": None,
                    "results": []}
        rows = s.query(d.RunJob).filter(d.RunJob.reason == newest.reason).all()

        done = [r for r in rows if r.status == "done"]
        dead = [r for r in rows if r.status == "dead"]
        live = [r for r in rows if r.status in ("queued", "running")]
        running = [r for r in rows if r.status == "running"]

        # WHICH running row is a worker actually on. See worker_liveness: a row
        # claimed by an instance that then died stays `running` on a live lease
        # with a frozen updated_at, and `current` used to be running[0] — an
        # arbitrary row out of an unordered list. The bar then named a review
        # nothing was processing and never advanced off it.
        _now = datetime.utcnow()
        _moving = [r for r in running if worker_liveness(r, _now)[0] == "running"]
        _frozen = [r for r in running if r not in _moving]
        # Frozen AND out of attempts: reap_abandoned's rows. These do NOT come
        # back on their own — that is the whole point of the reap — so the bar
        # must not tell a reader to wait for a retry that is not coming. In a
        # healthy app they exist for at most one drain tick; seeing them means
        # nothing is draining, which is exactly when the accurate sentence
        # matters.
        _stranded = [r for r in _frozen
                     if (r.attempts or 0) >= (r.max_attempts or 3)]
        _cur = max(_moving, key=lambda r: (r.updated_at or r.created_at or _now),
                   default=None)

        started = min((r.created_at for r in rows if r.created_at), default=None)
        finished = (max((r.updated_at for r in rows if r.updated_at), default=None)
                    if not live else None)

        # An ETA is only honest once something has finished: with no completed
        # run there is no rate to project from, and a number invented before
        # the first one lands is a guess wearing a clock.
        eta_s = None
        if live and done and started:
            elapsed = (datetime.utcnow() - started).total_seconds()
            per = elapsed / max(1, len(done))
            eta_s = int(per * len(live))

        return {
            "running": bool(live),
            "total": len(rows),
            # done counts FINISHED work, so a dead job is not counted as done.
            # A bar that reaches 100% while three reviews failed reads as a
            # clean run.
            "done": len(done),
            "failed": len(dead),
            "remaining": len(live),
            "current": (_cur.review_id if _cur is not None else ""),
            # WHAT AN EMPTY `current` MEANS, SAID OUT LOUD. It had three causes
            # wearing one blank: nothing claimed yet, every claim frozen on a
            # dead instance, and the batch being over. The bar rendered all
            # three as "starting" — the word for the first one only, and a lie
            # for the second, which is the state a restart mid-batch leaves.
            #   waiting  queued rows and no worker on any of them
            #   running  a worker is writing progress onto a row right now
            #   stalled  every running row is frozen; nothing is draining
            #   ""       the batch is not live
            "current_state": ("running" if _cur is not None
                              else "stalled" if _frozen
                              else "waiting" if live else ""),
            # Claimed rows that have stopped moving. They are still counted in
            # `remaining` because they ARE unfinished work — they become
            # reclaimable when their lease lapses — but they are not progress.
            "stalled": len(_frozen),
            "stranded": len(_stranded),
            "scope": "incomplete",
            "batch": newest.reason.split(":", 1)[-1],
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "eta_s": eta_s,
            "results": ([{"id": r.review_id, "ok": True, "error": ""} for r in done]
                        + [{"id": r.review_id, "ok": False,
                            "error": r.last_error or "the run failed"} for r in dead]),
        }
    finally:
        s.close()


def cancel_batch(reason_prefix: str = BULK_REASON_PREFIX) -> int:
    """Drop the QUEUED jobs of the newest batch. Returns how many were dropped.

    Only the queued ones: a running job holds a lease on an instance that is
    mid-pipeline, and killing its row would leave a half-written draft — the
    thing the old in-process cancel was careful about too. Those finish.
    """
    d = _db()
    s = d.SessionLocal()
    try:
        newest = (s.query(d.RunJob)
                   .filter(d.RunJob.reason.like(f"{reason_prefix}:%"))
                   .order_by(d.RunJob.created_at.desc()).first())
        if newest is None:
            return 0
        n = (s.query(d.RunJob)
              .filter(d.RunJob.reason == newest.reason,
                      d.RunJob.status == "queued")
              .update({d.RunJob.status: "dead",
                       d.RunJob.last_error: "cancelled before it started",
                       d.RunJob.updated_at: datetime.utcnow()},
                      synchronize_session=False))
        s.commit()
        return int(n or 0)
    finally:
        s.close()
