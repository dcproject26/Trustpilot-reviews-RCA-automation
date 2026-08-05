"""process_review must survive its OWN first statement failing.

`db = SessionLocal()` used to sit one line above the try that guards the rest
of the function. A pool timeout or an unreachable database therefore raised
straight out of process_review — past its handler, past `record_run_failure`,
past the finally that pops the progress entry. run_batch catches it now, so it
can no longer take the queue down, but the function was not self-consistent:
the one failure it could not report was the one that stopped it reporting
anything.

Everything here DRIVES process_review with a SessionLocal that fails the way a
dead pool fails. None of it asserts on the source text, which would pass just
as happily against a build where the line had moved back out again.
"""
import asyncio
import logging
import os
import tempfile

import pytest


@pytest.fixture()
def db_env(monkeypatch):
    """A throwaway SQLite DB with the real schema."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    yield db
    os.unlink(tmp.name)


class PoolExhausted(RuntimeError):
    """What SQLAlchemy raises when no connection comes free in time."""


def _seed_review(db, rid):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="David Test", body_original="awful",
                        reference_number=None, status="new"))
        s.commit()
    finally:
        s.close()


def _seed_draft(db, rid):
    """A draft row, so record_run_failure has somewhere to write."""
    s = db.SessionLocal()
    try:
        s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, confidence_trail=[]))
        s.commit()
    finally:
        s.close()


def _fails_forever():
    def factory():
        raise PoolExhausted("QueuePool limit of size 5 overflow 10 reached")
    return factory


def _fails_n_times(db, n):
    """Fails the first n calls, then hands back real sessions.

    This is the transient shape — a pool that was exhausted a moment ago and
    is not now — which is the case where the second chance inside
    record_run_failure is supposed to pay off.
    """
    real = db.SessionLocal
    state = {"n": n}

    def factory():
        if state["n"] > 0:
            state["n"] -= 1
            raise PoolExhausted("QueuePool limit of size 5 overflow 10 reached")
        return real()

    return factory


def _pipeline(db):
    import importlib
    import server.pipeline as P
    importlib.reload(P)
    return P


def test_an_unopenable_session_does_not_escape_the_function(db_env, monkeypatch):
    """The whole point. Before the fix this raised PoolExhausted at the caller."""
    db = db_env
    _seed_review(db, "tp_sess_1")
    P = _pipeline(db)
    monkeypatch.setattr(P, "SessionLocal", _fails_forever(), raising=False)

    asyncio.run(P.process_review("tp_sess_1"))  # must simply return


def test_the_failure_is_recorded_not_swallowed(db_env, monkeypatch, caplog):
    """A transient pool timeout still lands on the draft's confidence trail.

    record_run_failure opens its OWN session when the run never got one, which
    is a real second chance rather than a formality — so the run that could not
    start still leaves evidence that it tried.
    """
    db = db_env
    _seed_review(db, "tp_sess_2")
    _seed_draft(db, "tp_sess_2")
    P = _pipeline(db)
    # 1 = the run's own session. record_run_failure's own call then succeeds.
    monkeypatch.setattr(P, "SessionLocal", _fails_n_times(db, 1), raising=False)

    with caplog.at_level(logging.ERROR):
        asyncio.run(P.process_review("tp_sess_2"))

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(
            db.RcaDraft.review_id == "tp_sess_2").first()
        trail = list(d.confidence_trail or [])
        assert trail, (
            "the run died before it had a session and left nothing on the "
            "trail — a failed run is now indistinguishable from one nobody "
            "asked for")
        assert d.generated_at is not None, (
            "generated_at unstamped: the dashboard polls on it, so the card "
            "spins until it times out instead of showing the failure")
    finally:
        s.close()

    assert any("database session itself" in r.getMessage() for r in caplog.records), (
        "the log does not distinguish 'we could not open a session' from any "
        "other fatal error, which is the one thing this run knows")


def test_no_draft_row_says_so_rather_than_reporting_nothing(db_env, monkeypatch,
                                                            caplog):
    """A session failure with no draft to record it on is its own outcome.

    Distinct from a recorded failure, and distinct from a healthy run. If both
    logged the same line a reader could not tell which happened.
    """
    db = db_env
    _seed_review(db, "tp_sess_3")            # review, but deliberately no draft
    P = _pipeline(db)
    monkeypatch.setattr(P, "SessionLocal", _fails_n_times(db, 1), raising=False)

    with caplog.at_level(logging.ERROR):
        asyncio.run(P.process_review("tp_sess_3"))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("no draft row to record it on" in m for m in msgs), (
        "a run that died with nowhere to record it said nothing about that")


def test_a_dead_database_for_the_recording_too_still_says_so(db_env, monkeypatch,
                                                             caplog):
    """Both the run's session AND the recording's session fail.

    Nothing can be written anywhere, which is exactly when the log line has to
    carry the whole story — and when a silent return is worst.
    """
    db = db_env
    _seed_review(db, "tp_sess_4")
    _seed_draft(db, "tp_sess_4")
    P = _pipeline(db)
    monkeypatch.setattr(P, "SessionLocal", _fails_forever(), raising=False)

    with caplog.at_level(logging.ERROR):
        asyncio.run(P.process_review("tp_sess_4"))

    msgs = [r.getMessage() for r in caplog.records]
    assert any("unreachable for the recording too" in m for m in msgs), (
        "nothing was written and nothing said so")


def test_the_progress_entry_is_popped_even_when_the_session_never_opened(
        db_env, monkeypatch):
    """A leftover entry makes the review read as running for ever.

    The finally that pops it is the same finally that used to call
    db.close() unconditionally — which would have raised AttributeError on a
    None session and REPLACED the real error with a misleading one.
    """
    db = db_env
    _seed_review(db, "tp_sess_5")
    P = _pipeline(db)
    P.PIPELINE_PROGRESS["tp_sess_5"] = {"step": 0, "label": "queued"}
    monkeypatch.setattr(P, "SessionLocal", _fails_forever(), raising=False)

    asyncio.run(P.process_review("tp_sess_5"))

    assert "tp_sess_5" not in P.PIPELINE_PROGRESS, (
        "the review still reads as in flight after a run that never started")


def test_a_healthy_run_is_unaffected(db_env, monkeypatch):
    """The control. Moving the session must not change the ordinary path.

    Without this, every assertion above is satisfiable by a process_review that
    does nothing at all.
    """
    db = db_env
    _seed_review(db, "tp_sess_6")
    P = _pipeline(db)
    monkeypatch.setattr(P, "verify_bid", lambda bid: None, raising=False)

    asyncio.run(P.process_review("tp_sess_6"))

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(
            db.RcaDraft.review_id == "tp_sess_6").first()
        assert d is not None, "a normal run no longer leaves a draft row"
    finally:
        s.close()
    assert "tp_sess_6" not in P.PIPELINE_PROGRESS
