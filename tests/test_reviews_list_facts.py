"""The reviews list sends facts, not constants.

tests/test_bucket_parity.py checks the payload still MENTIONS each field the
client's fallback needs. That is a spelling check: `"has_draft": True` keeps
the string in the file, keeps the test green, and tells every client that
every review has been processed — which is the queued-review bug back, with
its guard intact.

Mutation testing found it by doing exactly that. So the facts are read off a
live response here instead.
"""
import os
import tempfile
from datetime import datetime

import pytest


@pytest.fixture()
def client(monkeypatch):
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

    s = db.SessionLocal()
    try:
        # One review the pipeline has not reached yet, and one it searched for
        # and missed. Same blank card; different fact.
        s.add(db.Review(id="tp_queued", slack_ts="1.0", slack_channel="C1",
                        rating=1, author="Queued", body_original="x",
                        status="new", received_at=datetime.utcnow()))
        s.add(db.Review(id="tp_missed", slack_ts="2.0", slack_channel="C1",
                        rating=1, author="Missed", body_original="x",
                        status="new", received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id="d_missed", review_id="tp_missed", booking={}))
        s.add(db.Review(id="tp_found", slack_ts="3.0", slack_channel="C1",
                        rating=1, author="Found", body_original="x",
                        status="new", received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id="d_found", review_id="tp_found", match_tier=1,
                          booking={"id": "32908218"}))
        s.commit()
    finally:
        s.close()

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        yield c
    os.unlink(tmp.name)


def _by_id(client, tab=None):
    r = client.get("/api/reviews" + (f"?tab={tab}" if tab else ""))
    assert r.status_code == 200, r.text
    return {row["id"]: row for row in r.json()}


def test_has_draft_is_a_fact_not_a_constant(client):
    rows = _by_id(client)
    assert rows["tp_queued"]["has_draft"] is False, (
        "the payload claims a review with no draft row has one, so every "
        "client falls back to filing it as untraceable")
    assert rows["tp_missed"]["has_draft"] is True
    assert rows["tp_found"]["has_draft"] is True


def test_the_bucket_tells_queued_from_missed(client):
    rows = _by_id(client)
    assert rows["tp_queued"]["bucket"] == "processing"
    assert rows["tp_missed"]["bucket"] == "untraceable"
    assert rows["tp_found"]["bucket"] == "identified"


def test_the_processing_tab_returns_only_queued_reviews(client):
    rows = _by_id(client, tab="processing")
    assert set(rows) == {"tp_queued"}, sorted(rows)


def test_the_untraceable_tab_no_longer_holds_queued_reviews(client):
    """The reported bug, at the API. Fifteen reviews appeared here at once
    after Refresh from Slack and every one of them was still being worked on."""
    rows = _by_id(client, tab="untraceable")
    assert "tp_queued" not in rows, (
        "a review that has not been searched is being served under the tab "
        "for reviews we searched for and could not find")
    assert set(rows) == {"tp_missed"}


def test_processing_state_says_which_kind_of_not_yet(client):
    rows = _by_id(client)
    q = rows["tp_queued"]
    assert q["processing_state"] in ("running", "stalled")
    assert q["processing_reason"], "no reason given for an empty card"
    # A review that WAS searched is not in a processing state at all.
    assert rows["tp_missed"]["processing_state"] == ""
    assert rows["tp_missed"]["processing_reason"] == ""


def test_a_stalled_run_says_to_re_run_it(client):
    """No progress entry exists for these ids, so they read as stalled — and
    that has to be actionable rather than merely true."""
    q = _by_id(client)["tp_queued"]
    assert q["processing_state"] == "stalled"
    assert "Re-run" in q["processing_reason"]
    assert "not a booking we could not find" in q["processing_reason"]


def test_a_running_pipeline_reads_as_running(client):
    import time

    import server.pipeline as P
    # updated_at is what makes this running rather than merely present: an
    # entry that has stopped moving is now reported as stopped.
    P.PIPELINE_PROGRESS["tp_queued"] = {"step": 4, "total": 8,
                                        "stage": "Insights",
                                        "started_at": time.time() - 9,
                                        "elapsed_s": 9,
                                        "updated_at": time.time()}
    try:
        q = _by_id(client)["tp_queued"]
        assert q["processing_state"] == "running"
        assert "Step 4 of 8" in q["processing_reason"]
        assert "not a failed match" in q["processing_reason"]
    finally:
        P.PIPELINE_PROGRESS.pop("tp_queued", None)


def test_every_review_appears_under_exactly_one_tab(client):
    """A review in two tabs shows the same card twice; a review in none is
    invisible, which is worse."""
    seen = {}
    for tab in ("bid", "possible_matches", "processing", "untraceable", "sent"):
        for rid in _by_id(client, tab=tab):
            seen[rid] = seen.get(rid, 0) + 1
    for rid in ("tp_queued", "tp_missed", "tp_found"):
        assert seen.get(rid) == 1, f"{rid} appears in {seen.get(rid, 0)} tabs"
