"""A run that stopped must be distinguishable from a run that is working.

The reported failure: a fresh ingest of fifteen reviews left thirteen sitting
in Processing and one card reading "Still running — nothing searched yet ·
Step 1 of 8 — matching booking" over a run that was dead. Nobody could tell
from the screen that anything was wrong, so the reviews sat there until
someone pressed Re-run on each of them by hand.

Two mechanisms, both rule one of CLAUDE.md:

  * every ingest path handed each review to Starlette as its OWN
    BackgroundTask, and Starlette runs them as `for task in self.tasks: await
    task()` with no try/except. The first task to raise drops every task
    behind it, and one wedged run holds the rest for as long as it lasts. The
    thirteen reviews had never been started, and nothing anywhere recorded
    that they were meant to be — a queued-and-never-started review carried
    exactly the evidence of a review nobody had ever asked about;

  * processing_state answered "is this run alive?" with "does a progress entry
    exist?". An entry is written at step 1 and removed in the run's `finally`,
    so the only run that could ever read as stalled was one that had already
    finished dying tidily. A run wedged inside a blocking model call never
    reaches its `finally` and reported itself as working for ever.

Everything here drives the real functions. The one source assertion is
negative, and says so.
"""
import asyncio
import os
import tempfile
import time
from datetime import datetime

import pytest

from server.tiers import (QUEUE_STALL_AFTER_S, STALL_AFTER_S, liveness,
                          processing_state)
from tests.conftest import drop_temp_db


class NS:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def _entry(**kw):
    now = time.time()
    base = {"step": 1, "total": 8, "stage": "matching booking",
            "started_at": now, "updated_at": now, "elapsed_s": 0,
            "queued": False}
    base.update(kw)
    return base


# ── The liveness judgement ──────────────────────────────────────────────────

def test_a_run_that_has_not_moved_is_not_called_running():
    """THE BUG. The entry is present and unchanged, which is precisely what a
    wedged run leaves behind — and it used to be the definition of running."""
    state, since = liveness(_entry(updated_at=time.time() - STALL_AFTER_S - 30))
    assert state == "stalled", (
        "a run that has not advanced a stage in over "
        f"{STALL_AFTER_S // 60} minutes still reports itself as working")
    assert since >= STALL_AFTER_S


def test_a_run_that_moved_a_moment_ago_is_running():
    """The other half. A fix that calls every run dead is the inverse bug."""
    state, since = liveness(_entry(updated_at=time.time() - 5))
    assert state == "running"
    assert since <= 10


def test_no_entry_is_not_the_same_answer_as_a_dead_entry():
    assert liveness(None)[0] == ""
    assert liveness({})[0] == ""


def test_an_entry_from_an_older_build_is_dated_by_its_start():
    """No updated_at at all. Falling back to started_at can only make a run
    look older than it is, never younger — the safe direction."""
    assert liveness({"step": 1, "started_at": time.time() - 5})[0] == "running"
    assert liveness({"step": 1,
                     "started_at": time.time() - STALL_AFTER_S - 5})[0] == "stalled"


def test_a_queued_review_is_its_own_state():
    state, _ = liveness(_entry(queued=True, updated_at=time.time() - 5))
    assert state == "queued", (
        "a review the runner has not started yet is being reported as one it "
        "is working on")


def test_a_review_queued_and_never_started_goes_stalled():
    state, since = liveness(
        _entry(queued=True, updated_at=time.time() - QUEUE_STALL_AFTER_S - 30))
    assert state == "stalled"
    assert since >= QUEUE_STALL_AFTER_S


def test_waiting_longer_than_a_run_may_is_still_allowed_in_the_queue():
    """A queued review is SUPPOSED to be waiting, so the run threshold must
    not be applied to it — fifteen reviews take longer than ten minutes to
    reach the back of the queue, and calling the last one dead is wrong."""
    assert liveness(
        _entry(queued=True, updated_at=time.time() - STALL_AFTER_S - 60)
    )[0] == "queued"


# ── What the card says ──────────────────────────────────────────────────────

def test_the_three_processing_states_do_not_read_the_same(monkeypatch):
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_run", _entry())
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_q",
                        _entry(queued=True, queue_position=4, queue_size=15,
                               queue_reason="slack-refresh"))
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_dead",
                        _entry(updated_at=time.time() - STALL_AFTER_S - 60))

    said = {}
    for rid in ("tp_run", "tp_q", "tp_dead", "tp_never"):
        said[rid] = processing_state(NS(id=rid, status="new"), None)

    assert said["tp_run"][0] == "running"
    assert said["tp_q"][0] == "queued"
    assert said["tp_dead"][0] == "stalled"
    assert said["tp_never"][0] == "stalled"
    sentences = [v[1] for v in said.values()]
    assert len(set(sentences)) == 4, f"two of these read the same: {sentences}"


def test_the_stall_threshold_is_announced_not_applied_quietly(monkeypatch):
    """Deciding a run is dead because it stopped moving is a JUDGEMENT. The
    reader is told the number it turned on and that nothing reported it."""
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_dead",
                        _entry(updated_at=time.time() - STALL_AFTER_S - 60))
    _, why = processing_state(NS(id="tp_dead", status="new"), None)
    assert f"{STALL_AFTER_S // 60} minutes" in why, why
    assert "judgement" in why.lower(), why
    assert "not something the run reported" in why, why
    assert "Re-run it" in why, why


def test_a_stalled_run_says_where_it_stopped(monkeypatch):
    """"It died" is not actionable; "it died at step 5, insights" is."""
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_dead",
                        _entry(step=5, stage="computing insights (BigQuery)",
                               updated_at=time.time() - STALL_AFTER_S - 60))
    _, why = processing_state(NS(id="tp_dead", status="new"), None)
    assert "step 5 of 8" in why, why
    assert "computing insights (BigQuery)" in why, why


def test_a_queued_review_says_what_it_is_waiting_behind(monkeypatch):
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_q",
                        _entry(queued=True, queue_position=13, queue_size=15,
                               queue_reason="slack-refresh"))
    state, why = processing_state(NS(id="tp_q", status="new"), None)
    assert state == "queued"
    assert "13 of 15" in why, why
    assert "slack-refresh" in why, why
    assert f"{QUEUE_STALL_AFTER_S // 60} minutes" in why, why


def test_a_finished_run_is_in_no_processing_state():
    """The inverse bug: a run that FINISHED (status flipped to draft) must not
    be described as a run in any state. A draft with the review still 'new' is
    now the dead-run case (A1) and is reported, so 'finished' turns on status,
    not merely on a draft existing."""
    assert processing_state(NS(id="x", status="draft"), NS(booking={})) == ("", "")


def test_a_drafted_run_left_new_is_reported_as_dead():
    """The A1 case: the early draft is written but the run never flipped the
    status to 'draft', and nothing is in progress — that is a death, not a
    finished review, and it must be said."""
    import server.pipeline as _P
    _P.PIPELINE_PROGRESS.pop("x2", None)
    state, why = processing_state(NS(id="x2", status="new"), NS(booking={}, confidence_trail=[]))
    assert state == "stalled" and why


# ── The heartbeat the judgement reads ───────────────────────────────────────

def test_progress_moves_the_heartbeat():
    import server.pipeline as P
    rid = "tp_hb"
    try:
        P._progress(rid, 1, "matching booking")
        first = P.PIPELINE_PROGRESS[rid]["updated_at"]
        time.sleep(0.02)
        P._progress(rid, 2, "fetching Zendesk timeline")
        second = P.PIPELINE_PROGRESS[rid]["updated_at"]
        assert second > first, (
            "the heartbeat does not move when the run advances a stage, so "
            "every run reads as stalled the moment it passes the threshold")
        assert liveness(P.PIPELINE_PROGRESS[rid])[0] == "running"
    finally:
        P.PIPELINE_PROGRESS.pop(rid, None)


def test_starting_a_run_re_dates_it_from_the_start_not_the_queue():
    """A review that waited eleven minutes in the queue and then started must
    not be born stalled, carrying the queue's clock into the run."""
    import server.pipeline as P
    rid = "tp_waited"
    try:
        P.mark_queued(rid, 15, 15, "slack-refresh")
        P.PIPELINE_PROGRESS[rid]["started_at"] = time.time() - STALL_AFTER_S - 120
        P.PIPELINE_PROGRESS[rid]["updated_at"] = time.time() - STALL_AFTER_S - 120
        P._progress(rid, 1, "matching booking")
        e = P.PIPELINE_PROGRESS[rid]
        assert e["queued"] is False
        assert liveness(e)[0] == "running", (
            "a run that finally started after a long wait is reported dead on "
            "arrival")
        assert e["queued_at"], "the wait is discarded rather than recorded"
    finally:
        P.PIPELINE_PROGRESS.pop(rid, None)


def test_mark_queued_makes_a_queued_review_visible():
    """The missing fact behind the thirteen stranded reviews: being queued has
    to leave a trace, or it is indistinguishable from never being asked for."""
    import server.pipeline as P
    rid = "tp_mq"
    try:
        assert processing_state(NS(id=rid, status="new"), None)[0] == "stalled"
        P.mark_queued(rid, 2, 15, "slack-refresh")
        state, why = processing_state(NS(id=rid, status="new"), None)
        assert state == "queued", state
        assert "2 of 15" in why, why
    finally:
        P.PIPELINE_PROGRESS.pop(rid, None)


# ── The batch runner ────────────────────────────────────────────────────────

@pytest.fixture()
def batch(monkeypatch):
    """server.pipeline with process_review replaced by a scriptable stub."""
    import server.pipeline as P
    ran = []

    def script(behaviour: dict, delay: float = 0.0):
        async def _fake(rid, force_candidates=False):
            ran.append(rid)
            P._progress(rid, 1, "matching booking")
            try:
                what = behaviour.get(rid)
                if what == "raise":
                    raise RuntimeError("QueuePool limit of size 5 reached")
                if what == "hang":
                    await asyncio.sleep(60)
                if delay:
                    await asyncio.sleep(delay)
            finally:
                P.PIPELINE_PROGRESS.pop(rid, None)
        monkeypatch.setattr(P, "process_review", _fake)
        return ran

    yield P, script
    for rid in list(P.PIPELINE_PROGRESS):
        P.PIPELINE_PROGRESS.pop(rid, None)


def test_one_failing_run_does_not_drop_the_ones_behind_it(batch):
    """THE BUG, at the runner. Starlette's own loop stops dead on the first
    raise; a fifteen-review ingest lost thirteen reviews that way."""
    P, script = batch
    ids = [f"tp_{i}" for i in range(5)]
    ran = script({"tp_1": "raise"})

    out = asyncio.run(P.run_batch(ids, "slack-refresh"))

    assert ran == ids, f"only {len(ran)} of 5 reviews were run: {ran}"
    assert out["completed"] == 4 and out["failed"] == 1, out
    assert out["queued"] == 5


def test_starlette_would_have_dropped_them(batch):
    """The control. Without the runner, the same five tasks lose three — this
    is the behaviour being fixed, asserted rather than described."""
    from starlette.background import BackgroundTasks
    ran = []

    def one(i):
        def _f():
            ran.append(i)
            if i == 1:
                raise RuntimeError("QueuePool limit of size 5 reached")
        return _f

    bt = BackgroundTasks()
    for i in range(5):
        bt.add_task(one(i))
    with pytest.raises(RuntimeError):
        asyncio.run(bt())
    assert ran == [0, 1], (
        "Starlette no longer drops the tasks behind a failure, so the runner "
        "this test justifies may no longer be needed")


def test_every_review_is_marked_queued_before_the_first_one_starts(batch):
    """The thirteen stranded reviews had no evidence they were ever queued.
    Marking happens up front, not as each run begins, so a batch killed
    halfway still shows what it was going to do."""
    P, script = batch
    ids = [f"tp_{i}" for i in range(4)]
    seen = {}

    async def _fake(rid, force_candidates=False):
        # Snapshot what the OTHER reviews look like while this one runs.
        seen[rid] = {r: processing_state(NS(id=r, status="new"), None)[0]
                     for r in ids}
        P.PIPELINE_PROGRESS.pop(rid, None)

    import server.pipeline as _P
    _P.process_review = _fake
    try:
        asyncio.run(P.run_batch(ids, "slack-refresh"))
    finally:
        pass

    assert seen["tp_0"]["tp_3"] == "queued", (
        f"while the first review ran, the last read as "
        f"{seen['tp_0']['tp_3']!r} — a review waiting its turn is "
        f"indistinguishable from one nobody ever queued")


def test_a_wedged_run_is_stopped_and_the_queue_moves_on(batch, monkeypatch):
    """One run that never returns held every review behind it. It is now
    bounded, and the bound is counted."""
    P, script = batch
    monkeypatch.setattr(P, "RUN_TIMEOUT_S", 0.05)
    ids = ["tp_a", "tp_hang", "tp_b"]
    ran = script({"tp_hang": "hang"})

    out = asyncio.run(P.run_batch(ids, "slack-refresh"))

    assert ran == ids, f"the queue stopped at the wedged run: {ran}"
    assert out["timed_out"] == 1 and out["completed"] == 2, out


def test_a_run_that_dies_before_it_starts_does_not_stay_queued(batch):
    """MUTATION SURVIVOR. process_review opens its session OUTSIDE its own
    try, so it can raise before ever reaching the `finally` that clears its
    progress entry — which is the exact failure that stopped the ingest. The
    review would then read as queued behind a runner that has already moved
    on, for as long as the process lives.

    The other batch tests all used a stub that pops its own entry, so the
    runner's own pop was never the thing under test.
    """
    P, _ = batch

    async def _die_before_starting(rid, force_candidates=False):
        raise RuntimeError("QueuePool limit of size 5 overflow 10 reached")

    import server.pipeline as _P
    _P.process_review = _die_before_starting
    out = asyncio.run(P.run_batch(["tp_early_death"], "slack-refresh"))

    assert out["failed"] == 1
    assert "tp_early_death" not in P.PIPELINE_PROGRESS, (
        "the review is still marked queued after the runner gave up on it")
    state, _why = processing_state(NS(id="tp_early_death", status="new"), None)
    assert state == "stalled", (
        f"a review whose run died before it started reads as {state!r}")


def test_the_watchdogs_sentence_is_not_truncated_like_a_stack_trace():
    """MUTATION SURVIVOR. _human_error clips an unknown exception at 160
    characters, which is right for a database error — the useful half is at
    the front — and wrong for a sentence we wrote ourselves, where the
    instruction is at the END. Relying on our text happening to fit is how the
    only actionable clause disappears the next time someone rewords it.
    """
    from server.pipeline import RunTimeout, _human_error
    long = ("we stopped this run after 12 minutes so the reviews queued behind "
            "it could go, and the eleven queued behind those too. That is our "
            "budget, not a failure any service reported. Re-run the review.")
    assert len(long) > 160, "rewrite the fixture; it no longer tests truncation"
    assert _human_error(RunTimeout(long)).endswith("Re-run the review."), (
        "the watchdog's own sentence is being clipped by the fallback meant "
        "for stack traces, so the reader is told what happened and not what "
        "to do about it")


def test_the_batch_leaves_no_review_marked_queued(batch):
    """A leftover queued entry is the same lie in the other direction: the
    review reads as waiting for a runner that has finished."""
    P, script = batch
    ids = ["tp_a", "tp_b"]
    script({"tp_a": "raise"})
    asyncio.run(P.run_batch(ids, "slack-refresh"))
    assert [r for r in ids if r in P.PIPELINE_PROGRESS] == []


def test_the_runner_carries_force_candidates_through(batch):
    """Re-run goes through the runner now, and Re-run's whole point is that
    the associate gets the picker back. Dropping the flag on the way through
    would silently auto-promote the best match to Tier 1 again."""
    P, _ = batch
    seen = []

    async def _fake(rid, force_candidates=False):
        seen.append(force_candidates)

    import server.pipeline as _P
    _P.process_review = _fake
    asyncio.run(P.run_batch(["tp_r"], "re-run", True))
    assert seen == [True], (
        "force_candidates does not reach the pipeline, so Re-run no longer "
        "reopens the candidate picker")
    seen.clear()
    asyncio.run(P.run_batch(["tp_r"], "slack-refresh"))
    assert seen == [False], "an ingest run is asking for the picker"


def test_an_empty_batch_says_so_rather_than_returning_silently(batch):
    P, script = batch
    script({})
    out = asyncio.run(P.run_batch([], "slack-refresh"))
    assert out == {"queued": 0, "completed": 0, "failed": 0, "timed_out": 0}


def test_the_batch_returns_a_counted_account(batch, monkeypatch):
    """"3 of 15 could not be run" beats a background task that simply ends."""
    P, script = batch
    monkeypatch.setattr(P, "RUN_TIMEOUT_S", 0.05)
    ids = ["tp_ok", "tp_bad", "tp_hang"]
    script({"tp_bad": "raise", "tp_hang": "hang"})
    out = asyncio.run(P.run_batch(ids, "slack-refresh"))
    assert out == {"queued": 3, "completed": 1, "failed": 1, "timed_out": 1}


def test_run_batch_sync_survives_a_runner_that_dies(monkeypatch):
    """The last resort. A background task raising is invisible: the response
    has gone, and nothing is watching the log."""
    import server.pipeline as P

    async def _boom(ids, reason="ingest"):
        raise RuntimeError("the loop itself died")

    monkeypatch.setattr(P, "run_batch", _boom)
    P.mark_queued("tp_z", 1, 1, "slack-refresh")
    out = P.run_batch_sync(["tp_z"], "slack-refresh")
    assert out["failed"] == 1
    assert "tp_z" not in P.PIPELINE_PROGRESS, (
        "a review left marked queued by a runner that died waits for ever")


# ── The failure record, and the timeout that uses it ────────────────────────

@pytest.fixture()
def live_db(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "true")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.pipeline as P
    importlib.reload(P)
    yield db, P
    drop_temp_db(tmp.name)


def _seed_draft(db, rid="tp_rec"):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="A", body_original="x", status="new",
                        received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, booking={},
                          confidence_trail=[{"mark": "pass", "text": "matched"}]))
        s.commit()
    finally:
        s.close()
    return rid


def test_a_failure_is_written_onto_the_draft(live_db):
    db, P = live_db
    rid = _seed_draft(db)
    assert P.record_run_failure(rid, RuntimeError("boom")) is True
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        assert d.confidence_trail[-1]["mark"] == "fail"
        assert d.generated_at is not None, (
            "generated_at is the dashboard's completion signal; a run that "
            "died without moving it leaves a spinner that never resolves")
    finally:
        s.close()


def test_the_recorded_failure_is_a_failure_entry(live_db):
    """The hop tests/test_rca_v4_persist.py stops following. failure_entry is
    what keeps a stack trace out of the panel and the raw text behind a toggle
    — a recorder that formatted its own line would lose both, and the source
    scan on the handler would still be green."""
    db, P = live_db
    rid = _seed_draft(db, "tp_shape")
    exc = RuntimeError("SELECT rca_drafts.id AS rca_drafts_id FROM rca_drafts")
    P.record_run_failure(rid, exc)
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        assert d.confidence_trail[-1] == P.failure_entry(exc), (
            "the recorder builds its own trail line instead of using "
            "failure_entry, so the panel gets whatever it decided to format")
    finally:
        s.close()


def test_no_draft_to_record_on_is_reported_not_swallowed(live_db):
    """False is a fact the caller logs. Returning None either way would make
    "recorded" and "there was nothing to record it on" the same outcome."""
    db, P = live_db
    assert P.record_run_failure("tp_nothing", RuntimeError("boom")) is False


def test_the_same_failure_is_not_stacked_twice(live_db):
    db, P = live_db
    rid = _seed_draft(db)
    P.record_run_failure(rid, RuntimeError("boom"))
    P.record_run_failure(rid, RuntimeError("boom"))
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        assert sum(1 for t in d.confidence_trail if t.get("mark") == "fail") == 1
    finally:
        s.close()


def test_a_run_stopped_by_the_watchdog_says_the_budget_was_ours(live_db,
                                                                monkeypatch):
    """A timeout is not a failure any service reported, and saying "Run failed
    — TimeoutError" over our own budget sends the reader to the wrong log."""
    db, P = live_db
    rid = _seed_draft(db, "tp_slow")
    monkeypatch.setattr(P, "RUN_TIMEOUT_S", 0.05)

    async def _hang(r, force_candidates=False):
        await asyncio.sleep(30)

    monkeypatch.setattr(P, "process_review", _hang)
    out = asyncio.run(P.run_batch([rid], "slack-refresh"))
    assert out["timed_out"] == 1

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        last = d.confidence_trail[-1]
        assert last["mark"] == "fail"
        assert "RunTimeout" in last["title"], last
        assert "our budget" in last["text"], last
        assert "Re-run" in last["text"], last
    finally:
        s.close()


def test_the_bulk_re_run_survives_a_review_that_never_returns(monkeypatch):
    """The bulk job already survived a review that RAISES. A review that never
    returns is the other way to stop a queue: one of them used to hold the
    runner while the job reported itself as still running.

    DRIVEN THROUGH run_batch, which is where this guarantee lives now. The
    bulk re-run used to have its own worker with its own timeout; it is durable
    job rows drained by run_batch (server/main.py::_job_runner) since the
    in-process version could not survive the container. The guarantee is
    unchanged and so is this test's point — one hanging review must not stop
    the queue, and the reason must say it was OUR budget rather than a failure
    any service reported."""
    import server.pipeline as P

    async def _hang(rid, force_candidates=False):
        await asyncio.sleep(30)

    monkeypatch.setattr(P, "process_review", _hang)
    monkeypatch.setattr(P, "RUN_TIMEOUT_S", 0.05)

    out = asyncio.run(asyncio.wait_for(
        P.run_batch(["tp_1", "tp_2"], "fix-incomplete"), 10))

    assert out["timed_out"] == 2, f"the queue did not survive the hang: {out}"
    assert out["completed"] == 0, out


# ── The wiring, at the endpoint ─────────────────────────────────────────────

def test_no_ingest_path_still_queues_one_task_per_review():
    """NEGATIVE source assertion — permitted by CLAUDE.md, and the only kind
    that unreachability cannot defeat. The per-review lambda is the shape that
    hands N separate tasks to Starlette's unguarded loop; if it comes back,
    the fifteen-review ingest loses thirteen reviews again."""
    src = open(os.path.join(os.path.dirname(__file__), "..", "server",
                            "api.py")).read()
    assert "asyncio.run(_pipeline(" not in src, (
        "an ingest path is queueing pipeline runs as one BackgroundTask each "
        "again; use server.pipeline.run_batch_sync")


@pytest.fixture()
def api_client(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "true")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c
    drop_temp_db(tmp.name)


def test_the_ingest_enqueues_one_durable_job_per_review(monkeypatch):
    """Fifteen reviews, fifteen DURABLE jobs, zero fire-and-forget tasks.

    The old shape handed the batch to a background task that ran after the
    response, in a container autoscale reclaims — which is how a fifteen-review
    ingest left thirteen reviews un-run (measured 0 of 9 on a re-run). Part B
    replaces that with a job row per review that the drain loop claims and that
    survives the request. The guarantee flips: not "one supervised task" but "no
    task at all — durable rows instead"."""
    import server.api as api
    import server.config as cfg
    from server.services import slack as slack_svc

    msgs = [{"ts": f"{i}.0", "text": f"review {i}"} for i in range(15)]

    class _Client:
        def conversations_history(self, **kw):
            return {"messages": msgs}

    monkeypatch.setattr(slack_svc, "_bot", _Client(), raising=False)
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_ORM", "C_ORM", raising=False)
    monkeypatch.setattr(slack_svc, "is_trustpilot_message", lambda ev: True)
    monkeypatch.setattr(slack_svc, "parse_review", lambda ev: {
        "slack_ts": ev["ts"], "slack_channel": "C_ORM", "rating": 1,
        "language": "en", "author": "A", "body_original": ev["text"],
        "reference_number": None})

    class _FakeDB:
        class _Q:
            def filter(self, *a):
                return self

            def first(self):
                return None
        def query(self, *a):
            return self._Q()

        def add(self, *a):
            pass

        def commit(self):
            pass

    enq = []
    monkeypatch.setattr(api.jobs, "enqueue",
                        lambda rid, reason="", force_candidates=False: enq.append(rid))

    from fastapi import BackgroundTasks
    bt = BackgroundTasks()
    out = asyncio.run(api.refresh_slack(hours=1, background_tasks=bt, db=_FakeDB()))

    assert out["queued"] == 15
    assert len(bt.tasks) == 0, (
        f"the ingest queued {len(bt.tasks)} background task(s); the fragile "
        f"fire-and-forget path is gone — runs are durable job rows now")
    assert enq == [f"tp_{i}_0" for i in range(15)], (
        f"each review must get its own durable job; got {enq}")


def test_the_model_call_cannot_block_for_half_an_hour():
    """The SDK defaults to a 600s read timeout and two retries. That is up to
    thirty minutes inside ONE call, which is how a run sat on "Step 1 of 8"
    while every review queued behind it waited."""
    from server.services import claude as C
    assert C.CALL_TIMEOUT_S <= 300, C.CALL_TIMEOUT_S
    assert C.CALL_MAX_RETRIES <= 1, C.CALL_MAX_RETRIES
    worst = C.CALL_TIMEOUT_S * (1 + C.CALL_MAX_RETRIES)
    import server.pipeline as P
    assert worst <= P.RUN_TIMEOUT_S, (
        f"one model call may block for {worst}s, which is longer than the "
        f"whole run is allowed ({P.RUN_TIMEOUT_S}s) — the watchdog would fire "
        f"on runs that are merely waiting on the first call")
    # And the client really carries them, rather than the constants being
    # decoration next to a default-configured client.
    _t = C._client.timeout
    assert float(getattr(_t, "read", _t)) <= 300, _t
    assert C._client.max_retries <= 1, C._client.max_retries


def test_the_model_call_does_not_block_the_event_loop(monkeypatch):
    """The SDK client is synchronous. Awaiting it directly froze the loop for
    the length of the HTTP request, which made every timeout above it
    undeliverable — a cancellation needs an await point, and there was none
    between entering the call and leaving it."""
    import threading

    from server.services import claude as C

    released = threading.Event()

    def _blocking(prompt, max_tokens):
        # Returns only once the LOOP has run something else. If the call is
        # made on the loop, nothing else can run and this times out.
        assert released.wait(5), "the event loop never got control back"
        return "answer"

    monkeypatch.setattr(C, "_messages_create", _blocking)
    monkeypatch.setattr(C, "MOCK_MODE", False)

    async def _drive():
        async def _release():
            await asyncio.sleep(0.05)
            released.set()
        out, _ = await asyncio.gather(C._call("prompt"), _release())
        return out

    assert asyncio.run(asyncio.wait_for(_drive(), 15)) == "answer"


def test_the_progress_endpoint_does_not_call_a_dead_run_running(api_client):
    """The re-run button counted up for ten minutes against a stage that had
    not moved since the first second, because `running` meant `bool(entry)`."""
    import server.pipeline as P
    try:
        P.PIPELINE_PROGRESS["tp_dead"] = _entry(
            updated_at=time.time() - STALL_AFTER_S - 60)
        got = api_client.get("/api/reviews/tp_dead/progress").json()
        assert got["state"] == "stalled", got
        assert got["running"] is False, got
        assert got["since_progress_s"] >= STALL_AFTER_S, got
        assert got["stalled_after_s"] == STALL_AFTER_S, got
    finally:
        P.PIPELINE_PROGRESS.pop("tp_dead", None)


def test_the_progress_endpoint_still_reports_a_live_run(api_client):
    import server.pipeline as P
    try:
        P.PIPELINE_PROGRESS["tp_live"] = _entry(step=6, stage="generating RCA")
        got = api_client.get("/api/reviews/tp_live/progress").json()
        assert got["running"] is True and got["state"] == "running", got
        assert got["step"] == 6 and got["stage"] == "generating RCA"
    finally:
        P.PIPELINE_PROGRESS.pop("tp_live", None)


def test_the_progress_endpoint_separates_queued_from_running(api_client):
    import server.pipeline as P
    try:
        P.mark_queued("tp_q", 7, 15, "slack-refresh")
        got = api_client.get("/api/reviews/tp_q/progress").json()
        assert got["state"] == "queued", got
        assert (got["queue_position"], got["queue_size"]) == (7, 15)
    finally:
        P.PIPELINE_PROGRESS.pop("tp_q", None)


def test_the_progress_endpoint_on_a_review_nobody_started(api_client):
    got = api_client.get("/api/reviews/tp_never/progress").json()
    assert got["running"] is False and got["state"] == ""
