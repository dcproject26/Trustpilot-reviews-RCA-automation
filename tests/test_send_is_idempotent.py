"""A second /send must not rewrite how the first one was recorded.

THE BUG. /send set sent_route from the state AT THE TIME IT RAN. On the first
send with a reply, nothing was posted yet, so sent_route was "reply" (reply +
RCA both went out). A second /send — a double-click, a retry, reopening a sent
card — saw rca_posted_at now set and recomputed sent_route to "rca_posted"
(RCA only, no reply), silently downgrading the record; a prior /close
("closed") was overwritten the same way. The RCA itself was not re-posted (a
later guard sees rca_posted_at), so only the recorded route degraded — the kind
of silent metadata drift the Sent tab and the export then report as fact.

/send is now idempotent: once a review is sent it returns what is already
recorded and changes nothing.
"""
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
        s.add(db.Review(id="tp_s", slack_ts="1.0", slack_channel="C1",
                        rating=1, author="Guest", body_original="bad",
                        status="draft", received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id="d_s", review_id="tp_s", match_tier=1,
                          booking={"id": "1"},
                          slack_thread_override="RCA TEXT"))
        s.commit()
    finally:
        s.close()

    async def _post_ok(channel, ts, text, as_user=False):
        return "1700000000.000100"
    monkeypatch.setattr(api, "post_to_thread", _post_ok)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as c:
        c.db = db
        yield c
    drop_temp_db(tmp.name)


def _route(client):
    s = client.db.SessionLocal()
    try:
        return s.query(client.db.Review).filter(
            client.db.Review.id == "tp_s").first().sent_route
    finally:
        s.close()


def test_the_first_send_records_reply_and_the_second_does_not_downgrade_it(client):
    first = client.post("/api/reviews/tp_s/send")
    assert first.status_code == 200, first.text
    assert first.json()["sent_route"] == "reply", first.json()
    assert _route(client) == "reply"

    second = client.post("/api/reviews/tp_s/send")
    assert second.status_code == 200, second.text
    assert second.json().get("already_sent") is True, second.json()
    assert second.json()["sent_route"] == "reply", (
        f"the second send downgraded the route: {second.json()}")
    assert _route(client) == "reply", "the stored route was rewritten by a re-send"


def test_a_second_send_does_not_overwrite_a_close(client):
    """A review finished with /close is recorded 'closed'. A stray /send after
    it must not turn that into a send route."""
    closed = client.post("/api/reviews/tp_s/close", json={"reason": "no reply needed"})
    assert closed.status_code == 200, closed.text
    assert _route(client) == "closed", _route(client)

    client.post("/api/reviews/tp_s/send")
    assert _route(client) == "closed", "a /send after /close rewrote the route"
