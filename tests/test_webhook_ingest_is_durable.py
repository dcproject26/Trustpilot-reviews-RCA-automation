"""A review arriving from Slack must get a run that survives the container.

THE GAP THIS CLOSES. Every run path in api.py was converted to durable jobs —
and `server/webhook.py`, the path reviews ACTUALLY arrive through, was missed.
It still did `background_tasks.add_task(_run, review_id)`, a task executed after
the response is sent, inside that request's process. On autoscale the container
is reclaimed at exactly that moment, so a freshly ingested review got a run that
never happened: status "new", no draft row, nothing on the card explaining it.
Converting api.py alone left the backlog looking fixed while every NEW review
kept landing in the same state.

This file exists partly because webhook.py had NO tests at all, which is how a
whole ingest path was converted-around rather than converted.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture()
def client(live_db, monkeypatch):
    """The webhook router over this test's throwaway database."""
    import server.config as cfg
    import server.webhook as wh
    from server.services import slack as slack_svc

    monkeypatch.setattr(cfg, "MOCK_MODE", True, raising=False)
    monkeypatch.setattr(wh, "MOCK_MODE", True, raising=False)
    monkeypatch.setattr(slack_svc, "is_trustpilot_message", lambda ev: True)
    monkeypatch.setattr(wh, "is_trustpilot_message", lambda ev: True, raising=False)
    # The handler only looks at message events on a configured ORM channel.
    monkeypatch.setattr(wh, "ORM_CHANNELS", ["C_ORM"], raising=False)
    monkeypatch.setattr(wh, "parse_review", lambda ev: {
        "slack_ts": ev.get("ts", "1.0"), "slack_channel": "C_ORM", "rating": 1,
        "language": "en", "author": "A", "body_original": "a bad time",
        "reference_number": None, "published_at": None,
        "published_at_source": ""}, raising=False)

    app = FastAPI()
    app.include_router(wh.router)
    with TestClient(app) as c:
        yield c


def _event(ts="1.0", event_id="ev_1"):
    return {"type": "event_callback", "event_id": event_id,
            "event": {"type": "message", "ts": ts, "channel": "C_ORM",
                      "text": "review"}}


def test_an_ingested_review_gets_a_durable_job_not_a_background_task(client, live_db):
    """THE POINT. The row is in the database, so the run survives the instance
    that accepted the webhook."""
    r = client.post("/webhook/slack", json=_event())
    assert r.status_code == 200, r.text
    rid = r.json()["review_id"]

    s = live_db.SessionLocal()
    try:
        job = (s.query(live_db.RunJob)
                 .filter(live_db.RunJob.review_id == rid).first())
        assert job is not None, (
            "the webhook ingested a review and queued no durable job — the run "
            "dies with the container that served the webhook")
        assert job.status in ("queued", "running")
    finally:
        s.close()


def test_the_review_itself_is_still_written(client, live_db):
    """The ingest must not have been broken by the change."""
    rid = client.post("/webhook/slack", json=_event()).json()["review_id"]
    s = live_db.SessionLocal()
    try:
        assert s.query(live_db.Review).filter(live_db.Review.id == rid).first()
    finally:
        s.close()


def test_a_duplicate_delivery_does_not_queue_a_second_run(client, live_db):
    """Slack retries. Two deliveries of one event must not run the pipeline
    twice on the same review, both writing the same draft."""
    first = client.post("/webhook/slack", json=_event(event_id="ev_dupe"))
    client.post("/webhook/slack", json=_event(event_id="ev_dupe"))
    rid = first.json()["review_id"]
    s = live_db.SessionLocal()
    try:
        n = (s.query(live_db.RunJob)
               .filter(live_db.RunJob.review_id == rid).count())
        assert n == 1, f"{n} jobs queued for one review"
    finally:
        s.close()


def test_the_dev_test_endpoint_is_durable_too(client, live_db):
    r = client.post("/webhook/test", json={"review_id": "tp_manual"})
    assert r.status_code == 200
    s = live_db.SessionLocal()
    try:
        assert (s.query(live_db.RunJob)
                  .filter(live_db.RunJob.review_id == "tp_manual").first())
    finally:
        s.close()


def test_no_ingest_path_still_schedules_a_background_run():
    """NEGATIVE source assertion, and it says so (CLAUDE.md rule 2).

    Unreachability cannot defeat "this string appears nowhere". A new handler
    copying the old pattern reintroduces the exact defect, and every other test
    here would still pass.
    """
    import pathlib
    src = pathlib.Path("server/webhook.py").read_text(encoding="utf-8")
    assert "background_tasks.add_task" not in src, (
        "webhook.py schedules a run as a background task again; those die with "
        "the container that served the request")
