"""Part B — a run survives the container that started it.

Part A measured the defect: every run was `background_tasks.add_task`, executed
after the response in a container autoscale reclaims once the request is done,
so 0 of 9 re-runs moved a field. A durable `run_jobs` row any instance can
claim, a lease so an unfinished claim is reclaimable, bounded retries so a job
that always fails becomes `dead` with a reason (not a silent retry loop), and
DB-backed job state so a run on another instance is not read as a dead one.

Driven against the real functions and a real database (live_db).
"""
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace as NS

from server import jobs
from server.tiers import processing_state


def _job(live_db, jid):
    s = live_db.SessionLocal()
    try:
        return s.query(live_db.RunJob).get(jid)
    finally:
        s.close()


# ── enqueue + dedupe ────────────────────────────────────────────────────────

def test_enqueue_creates_one_queued_job(live_db):
    jid = jobs.enqueue("tp_1", "re-run", True)
    j = _job(live_db, jid)
    assert j.status == "queued" and j.review_id == "tp_1"
    assert j.force_candidates is True and j.attempts == 0


def test_enqueue_dedupes_an_active_review(live_db):
    a = jobs.enqueue("tp_dup", "re-run")
    b = jobs.enqueue("tp_dup", "manual-add")
    assert a == b, "a second enqueue for an active review made a duplicate job"
    s = live_db.SessionLocal()
    n = s.query(live_db.RunJob).filter_by(review_id="tp_dup").count()
    s.close()
    assert n == 1


# ── atomic claim + lease ────────────────────────────────────────────────────

def test_claim_takes_the_job_once(live_db):
    jobs.enqueue("tp_c", "re-run")
    first = jobs.claim_next("w1")
    assert first and first["review_id"] == "tp_c"
    j = _job(live_db, first["id"])
    assert j.status == "running" and j.attempts == 1 and j.claimed_by == "w1"
    assert jobs.claim_next("w2") is None, "the same job was claimed twice"


def test_a_live_lease_is_not_reclaimed(live_db):
    jobs.enqueue("tp_live", "re-run")
    jobs.claim_next("w1")                 # lease is now + LEASE_S (future)
    assert jobs.claim_next("w2") is None, "a live-leased job was reclaimed"


def test_a_lapsed_lease_is_reclaimable_and_counts_the_attempt(live_db):
    jobs.enqueue("tp_lapse", "re-run")
    c = jobs.claim_next("w1")
    s = live_db.SessionLocal()
    s.query(live_db.RunJob).filter_by(id=c["id"]).update(
        {live_db.RunJob.lease_expires_at: datetime.utcnow() - timedelta(minutes=1)})
    s.commit()
    s.close()
    again = jobs.claim_next("w2")
    assert again and again["id"] == c["id"], "a lapsed-lease job was not reclaimable"
    j = _job(live_db, c["id"])
    assert j.attempts == 2 and j.claimed_by == "w2"


# ── bounded retries ─────────────────────────────────────────────────────────

def test_a_job_dies_after_max_attempts_with_its_reason(live_db):
    jid = jobs.enqueue("tp_retry", "re-run")
    outcomes = []
    for _ in range(3):
        jobs.claim_next("w")              # attempts -> 1, 2, 3
        outcomes.append(jobs.fail(jid, "boom: it broke"))
    assert outcomes == ["queued", "queued", "dead"], outcomes
    j = _job(live_db, jid)
    assert j.status == "dead" and "boom" in (j.last_error or "")


def test_a_failure_with_attempts_left_is_requeued(live_db):
    jid = jobs.enqueue("tp_rq", "re-run")
    jobs.claim_next("w")                  # attempts 1 of 3
    assert jobs.fail(jid, "transient") == "queued"
    j = _job(live_db, jid)
    assert j.status == "queued" and j.lease_expires_at is None
    assert j.last_error == "transient"


# ── DB-backed state (cross-instance) ────────────────────────────────────────

def test_job_states_reports_queued_and_running(live_db):
    jobs.enqueue("tp_q", "re-run")
    jobs.enqueue("tp_r", "re-run")
    jobs.claim_next("w")                  # oldest becomes running
    assert sorted(jobs.job_states().values()) == ["queued", "running"]


def test_a_lapsed_running_job_is_not_reported_as_running(live_db):
    jid = jobs.enqueue("tp_lapsed2", "re-run")
    jobs.claim_next("w")
    s = live_db.SessionLocal()
    s.query(live_db.RunJob).filter_by(id=jid).update(
        {live_db.RunJob.lease_expires_at: datetime.utcnow() - timedelta(minutes=1)})
    s.commit()
    s.close()
    assert "tp_lapsed2" not in jobs.job_states(), "a dead-lease job still reads as running"


def test_note_progress_writes_to_the_running_job(live_db):
    jid = jobs.enqueue("tp_prog", "re-run")
    jobs.claim_next("w")
    jobs._ACTIVE["tp_prog"] = jid          # this process is running it
    try:
        jobs.note_progress("tp_prog", 3, 8, "zendesk")
    finally:
        jobs._ACTIVE.pop("tp_prog", None)
    j = _job(live_db, jid)
    assert j.progress and j.progress["step"] == 3 and j.progress["stage"] == "zendesk"


# ── the drain loop ──────────────────────────────────────────────────────────

def _stop_after(n):
    st = {"i": 0}
    def stop():
        st["i"] += 1
        return st["i"] > n
    return stop


def test_the_drain_loop_runs_a_queued_job_and_marks_it_done(live_db):
    jid = jobs.enqueue("tp_drain", "re-run")
    ran = []

    async def runner(job):
        ran.append(job["review_id"])

    async def go():
        await jobs.run_drain_loop(runner, "w", idle_sleep_s=0.01,
                                  should_stop=_stop_after(3))
    asyncio.run(asyncio.wait_for(go(), timeout=5))
    assert ran == ["tp_drain"]
    assert _job(live_db, jid).status == "done"


def test_the_drain_loop_marks_a_forever_failing_job_dead(live_db):
    jid = jobs.enqueue("tp_drain_fail", "re-run")

    async def runner(job):
        raise RuntimeError("kaboom")

    async def go():
        await jobs.run_drain_loop(runner, "w", idle_sleep_s=0.01,
                                  should_stop=_stop_after(8))
    asyncio.run(asyncio.wait_for(go(), timeout=5))
    j = _job(live_db, jid)
    assert j.status == "dead", f"a run that always raises did not become dead: {j.status}"
    assert "kaboom" in (j.last_error or "")


# ── processing_state honours the cross-instance job state ───────────────────

def test_processing_state_reads_a_run_on_another_instance_as_running():
    import server.pipeline as pipe
    pipe.PIPELINE_PROGRESS.pop("tp_ji", None)
    st, why = processing_state(NS(id="tp_ji", status="new"), None, job_state="running")
    assert st == "running" and "another server" in why


def test_processing_state_reads_a_queued_job_as_queued():
    import server.pipeline as pipe
    pipe.PIPELINE_PROGRESS.pop("tp_jq", None)
    st, why = processing_state(NS(id="tp_jq", status="new"), None, job_state="queued")
    assert st == "queued" and why
