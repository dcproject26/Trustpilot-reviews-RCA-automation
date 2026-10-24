"""parse_review captures the Trustpilot review URL from wherever it sits."""
from server.services.slack import parse_review

URL = "https://www.trustpilot.com/reviews/64f1a2b3c4d5e6f7a8b9c0d1"


def _ev(**over):
    base = {"ts": "1.0", "channel": "C1", "text": "", "blocks": [], "attachments": []}
    base.update(over)
    return base


def test_url_from_attachment_action_button():
    ev = _ev(attachments=[{"text": "body", "actions": [
        {"type": "button", "text": "Reply", "url": URL + "?utm=x"}]}])
    assert parse_review(ev)["review_url"] == URL


def test_url_from_attachment_title_link():
    ev = _ev(attachments=[{"title": "Great", "title_link": URL, "text": "body"}])
    assert parse_review(ev)["review_url"] == URL


def test_url_from_message_text():
    ev = _ev(text=f"New review <{URL}|view> by Jo")
    assert parse_review(ev)["review_url"] == URL


def test_url_from_block_accessory_and_author_link():
    ev = _ev(blocks=[{"type": "section", "text": {"text": "x"},
                      "accessory": {"type": "button", "url": URL}}])
    assert parse_review(ev)["review_url"] == URL
    ev2 = _ev(attachments=[{"author_link": URL, "text": "b"}])
    assert parse_review(ev2)["review_url"] == URL


def test_none_when_no_trustpilot_review_link():
    ev = _ev(text="see https://example.com/reviews/abc and https://www.trustpilot.com/users/xyz",
             attachments=[{"title_link": "https://example.com", "text": "b"}])
    assert parse_review(ev)["review_url"] is None


# -- persistence: the URL must reach the Review row on both ingest paths -----

def test_sync_ingest_persists_review_url(live_db, monkeypatch):
    import asyncio
    import server.services.slack as slk
    from server.db import Review

    class _C:
        def conversations_history(self, **kw):
            return {"messages": [{"ts": "1750000000.000100", "text": "x",
                                  "attachments": [{"title": "T", "title_link": URL,
                                                   "text": "body", "footer": "★★"}]}]}
    monkeypatch.setattr(slk, "_bot", _C())
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "C_ORM")
    monkeypatch.setattr(slk, "is_trustpilot_message", lambda ev: True)
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(slk.sync_channel_to_db(s, hours=1))
        assert r["ok"]
        rows = s.query(Review).all()
        assert len(rows) == 1 and rows[0].review_url == URL, r
    finally:
        s.close()


def test_webhook_ingest_persists_review_url(live_db, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import server.config as cfg
    import server.webhook as wh
    monkeypatch.setattr(cfg, "MOCK_MODE", True, raising=False)
    monkeypatch.setattr(wh, "MOCK_MODE", True, raising=False)
    monkeypatch.setattr(wh, "is_trustpilot_message", lambda ev: True, raising=False)
    monkeypatch.setattr(wh, "ORM_CHANNELS", ["C_ORM"], raising=False)
    app = FastAPI()
    app.include_router(wh.router)
    with TestClient(app) as c:
        r = c.post("/webhook/slack", json={
            "type": "event_callback", "event_id": "ev_url",
            "event": {"type": "message", "ts": "1750000001.0", "channel": "C_ORM",
                      "text": "r", "attachments": [{"title_link": URL, "text": "b"}]}})
        assert r.status_code == 200, r.text
        rid = r.json()["review_id"]
    s = live_db.SessionLocal()
    try:
        assert s.query(live_db.Review).filter_by(id=rid).first().review_url == URL
    finally:
        s.close()


def test_review_url_column_is_in_the_auto_migration():
    from server.db import _WANTED_REVIEW_COLUMNS
    assert _WANTED_REVIEW_COLUMNS(False)["review_url"] == "TEXT"


def test_url_from_block_elements_button():
    ev = _ev(blocks=[{"type": "actions", "elements": [{"type": "button", "url": URL}]}])
    assert parse_review(ev)["review_url"] == URL


def test_url_without_www_is_captured():
    u = "https://trustpilot.com/reviews/abc123"
    assert parse_review(_ev(text=f"see {u}"))["review_url"] == u
