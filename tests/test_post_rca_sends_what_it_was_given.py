"""The text that reaches Slack is the text the dashboard sent.

The browser tests for this intercept the /post-rca route, so they prove the
request carries the right body and stop there — the server was free to ignore
it, and mutation testing showed that it could: deleting the read of body.text
left the whole suite green. Two halves of one guarantee, and only one of them
was being checked.

This half calls the endpoint for real and reads what post_to_thread was
handed.
"""
import os
import tempfile
from datetime import datetime

import pytest
from tests.conftest import drop_temp_db


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
        s.add(db.Review(id="tp_post", slack_ts="1.0", slack_channel="C1",
                        rating=1, author="Guest", body_original="bad",
                        status="draft", received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id="d_post", review_id="tp_post", match_tier=1,
                          booking={"id": "32908218"},
                          slack_thread_override="SAVED OVERRIDE"))
        s.commit()
    finally:
        s.close()

    sent = []

    async def _capture(channel, ts, text, as_user=False):
        sent.append(text)
        return "1700000000.000100"

    monkeypatch.setattr(api, "post_to_thread", _capture)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        c.sent = sent
        c.db = db
        yield c
    drop_temp_db(tmp.name)


def test_the_text_in_the_request_is_the_text_that_is_posted(client):
    r = client.post("/api/reviews/tp_post/post-rca",
                    json={"text": "ONLY THE INSIGHTS SECTION"})
    assert r.status_code == 200, r.text
    assert client.sent == ["ONLY THE INSIGHTS SECTION"], (
        f"the server posted something other than what it was sent: "
        f"{client.sent}")


def test_the_saved_override_is_used_when_no_text_is_sent(client):
    r = client.post("/api/reviews/tp_post/post-rca", json={})
    assert r.status_code == 200, r.text
    assert client.sent == ["SAVED OVERRIDE"]


def test_a_body_less_post_still_works(client):
    """Older clients, and anything scripted. It must not 422."""
    r = client.post("/api/reviews/tp_post/post-rca")
    assert r.status_code == 200, r.text
    assert client.sent == ["SAVED OVERRIDE"]


def test_posting_saves_what_it_posted(client):
    """Otherwise the thread and the dashboard show different posts and
    neither is wrong about what it holds."""
    client.post("/api/reviews/tp_post/post-rca",
                json={"text": "TRIMMED TO ONE SECTION"})
    s = client.db.SessionLocal()
    try:
        d = s.query(client.db.RcaDraft).filter(
            client.db.RcaDraft.review_id == "tp_post").first()
        assert d.slack_thread_override == "TRIMMED TO ONE SECTION", (
            f"the post went to Slack but the card still shows "
            f"{d.slack_thread_override!r}")
    finally:
        s.close()


def test_an_empty_text_does_not_wipe_the_saved_post(client):
    """A caller sending "" means "no opinion", not "post nothing". Treating it
    as an instruction would clear an override somebody had typed."""
    client.post("/api/reviews/tp_post/post-rca", json={"text": "   "})
    assert client.sent == ["SAVED OVERRIDE"]
    s = client.db.SessionLocal()
    try:
        d = s.query(client.db.RcaDraft).filter(
            client.db.RcaDraft.review_id == "tp_post").first()
        assert d.slack_thread_override == "SAVED OVERRIDE"
    finally:
        s.close()


def test_a_second_post_is_refused_unless_asked_for(client):
    client.post("/api/reviews/tp_post/post-rca", json={"text": "FIRST"})
    r = client.post("/api/reviews/tp_post/post-rca", json={"text": "SECOND"})
    assert r.json().get("already_posted") is True
    assert client.sent == ["FIRST"], "a second copy went into the thread"
    r = client.post("/api/reviews/tp_post/post-rca?force=true",
                    json={"text": "SECOND"})
    assert r.status_code == 200
    assert client.sent == ["FIRST", "SECOND"]
