"""The Reporting page's report endpoints: preview (read-only) and send. Driven
through the real app, with Slack posting stubbed so no message leaves."""
from datetime import datetime

import server.config as cfg
import server.services.slack as slack

# A window no other test writes into — these assert on their own rows.
W = {"date_from": "2027-05-04", "date_to": "2027-05-04", "preset": "custom"}


def _seed():
    from server.db import Review, RcaDraft, SessionLocal
    s = SessionLocal()
    try:
        s.add(Review(id="rp1", received_at=datetime(2027, 5, 4, 9), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="rp1-d", review_id="rp1", match_tier=1,
                       booking={"id": "B1"}, sent_at=datetime(2027, 5, 4, 15)))
        s.add(Review(id="rp2", received_at=datetime(2027, 5, 4, 10), status="new", rating=1))
        s.commit()
    finally:
        s.close()


def test_preview_builds_the_draft_and_names_the_channel(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    _seed()
    r = client.post("/api/reporting/report/preview", json={**W, "sections": ["tier"]})
    assert r.status_code == 200
    d = r.json()
    assert d["channel"] == "C045KG5AJF5"
    assert d["date_from"] == "2027-05-04" and d["reviews"] == 2
    assert "Received: *2*" in d["text"] and "• Tier 1 — 1" in d["text"]


def test_preview_posts_nothing(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    posted = []
    monkeypatch.setattr(slack, "post_to_channel", lambda ch, t: posted.append(t) or "ts")
    client.post("/api/reporting/report/preview", json=W)
    assert posted == []


def test_preview_rejects_a_bad_section_with_a_reason(client):
    r = client.post("/api/reporting/report/preview", json={**W, "sections": ["nope"]})
    assert r.status_code == 422 and "unknown section" in r.json()["detail"]


def test_send_posts_the_edited_text_verbatim(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    sent = {}
    monkeypatch.setattr(slack, "post_to_channel",
                        lambda ch, t: (sent.update(channel=ch, text=t), "ts_1")[1])
    r = client.post("/api/reporting/report/send", json={**W, "text": "MY EDIT"})
    assert r.status_code == 200 and r.json()["ts"] == "ts_1"
    # what the person read on screen is exactly what posts
    assert sent == {"channel": "C045KG5AJF5", "text": "MY EDIT"}


def test_send_rebuilds_only_when_the_draft_is_blank(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    _seed()
    sent = {}
    monkeypatch.setattr(slack, "post_to_channel",
                        lambda ch, t: (sent.update(text=t), "ts_2")[1])
    r = client.post("/api/reporting/report/send", json={**W, "text": "   "})
    assert r.status_code == 200
    assert "*ORM Report*" in sent["text"]          # rebuilt, not posted blank


def test_send_without_a_channel_is_a_clear_400(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "")
    r = client.post("/api/reporting/report/send", json={**W, "text": "x"})
    assert r.status_code == 400 and "SLACK_CHANNEL_DAILY" in r.json()["detail"]


def test_send_reports_why_slack_refused(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    monkeypatch.setattr(slack, "post_to_channel", lambda ch, t: None)
    monkeypatch.setattr(slack, "last_post_failure", {"why": "bot not in channel"})
    r = client.post("/api/reporting/report/send", json={**W, "text": "x"})
    assert r.status_code == 502 and "bot not in channel" in r.json()["detail"]
