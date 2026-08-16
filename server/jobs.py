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


async def run_drain_loop(runner, worker_id: str, idle_sleep_s: float = 15.0,
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
