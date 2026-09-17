"""The daily-digest endpoints: preview (read-only), manual send, and the
token-guarded external trigger. Driven through the real app + a real-schema DB
(the `client` fixture), with Slack posting stubbed so no message leaves."""
import server.config as cfg
import server.services.slack as slack
from server.services import daily_report


def test_preview_returns_text_and_channel(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    r = client.get("/api/reports/daily/preview")
    assert r.status_code == 200
    data = r.json()
    assert data["channel"] == "C045KG5AJF5"
    assert "*ORM Daily —" in data["text"]
    # The dashboard renders this text verbatim, so the restructured blocks must
    # survive the endpoint — the shape ({text, channel}) is unchanged, the body
    # is not. An empty DB still shows the backlog headline and the 24h frame.
    text = data["text"]
    assert "Total pending reviews: *0*" in text
    assert "• Received today — *0*" in text
    assert "• Solved today — *0*" in text
    assert "Solved by tier" in text
    # No cross-cohort ratio anywhere, and no percentage invented out of an empty
    # cohort (0 of 0 is unanswerable, not 0%).
    assert "%" not in text


def test_send_without_channel_is_a_clear_400(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "")
    r = client.post("/api/reports/daily/send", json={"text": "hello"})
    assert r.status_code == 400
    assert "SLACK_CHANNEL_DAILY" in r.json()["detail"]


def test_send_posts_the_edited_text(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    sent = {}
    def fake_post(channel, text):
        sent["channel"], sent["text"] = channel, text
        return "ts_123"
    monkeypatch.setattr(slack, "post_to_channel", fake_post)
    r = client.post("/api/reports/daily/send", json={"text": "EDITED BODY"})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ts": "ts_123", "channel": "C045KG5AJF5"}
    # The associate's edit is what goes out, verbatim — not a rebuilt digest.
    assert sent == {"channel": "C045KG5AJF5", "text": "EDITED BODY"}


def test_send_blank_text_rebuilds_a_fresh_digest(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    sent = {}
    monkeypatch.setattr(slack, "post_to_channel",
                        lambda ch, txt: sent.setdefault("text", txt) or "ts_9")
    r = client.post("/api/reports/daily/send", json={"text": "   "})
    assert r.status_code == 200
    # Empty edit -> the server builds the real digest rather than posting blank.
    assert "*ORM Daily —" in sent["text"]


def test_send_reports_why_slack_refused(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    monkeypatch.setattr(slack, "post_to_channel", lambda ch, txt: None)
    monkeypatch.setattr(slack, "last_post_failure",
                        {"why": "bot not in channel"})
    r = client.post("/api/reports/daily/send", json={"text": "x"})
    assert r.status_code == 502
    assert "bot not in channel" in r.json()["detail"]


# ── external trigger: the token gate ────────────────────────────────────────

def test_trigger_disabled_when_no_token_configured(client, monkeypatch):
    monkeypatch.setattr(cfg, "DAILY_REPORT_TOKEN", "")
    r = client.post("/api/reports/daily")
    assert r.status_code == 503


def test_trigger_rejects_a_wrong_token(client, monkeypatch):
    monkeypatch.setattr(cfg, "DAILY_REPORT_TOKEN", "secret")
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    assert client.post("/api/reports/daily").status_code == 401
    assert client.post(
        "/api/reports/daily",
        headers={"X-Report-Token": "nope"}).status_code == 401
    assert client.post(
        "/api/reports/daily",
        headers={"Authorization": "Bearer nope"}).status_code == 401


def test_trigger_accepts_bearer_and_x_header(client, monkeypatch):
    monkeypatch.setattr(cfg, "DAILY_REPORT_TOKEN", "secret")
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    monkeypatch.setattr(slack, "post_to_channel", lambda ch, txt: "ts_ok")
    r1 = client.post("/api/reports/daily",
                     headers={"Authorization": "Bearer secret"})
    r2 = client.post("/api/reports/daily",
                     headers={"X-Report-Token": "secret"})
    assert r1.status_code == 200 and r1.json()["ok"] is True
    assert r2.status_code == 200 and r2.json()["ts"] == "ts_ok"


def test_preview_appends_extra_category_sections(client, live_db, monkeypatch):
    """A person composing the report can append category breakdowns via
    ?sections=; the endpoint threads them into the same build the auto-post
    uses, so the appended numbers come from the one query engine."""
    import server.config as cfg
    from datetime import datetime
    from server.db import Review, RcaDraft
    from server.services.daily_report import _db_bounds
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    # plant reviews inside the current daily window so the section has content
    start, end = _db_bounds(datetime.utcnow())
    mid = start + (end - start) / 2
    s = live_db.SessionLocal()
    try:
        for i in range(4):
            rid = f"api_x{i}"
            s.add(Review(id=rid, received_at=mid, status="sent", rating=1,
                         picked_up_by="Avi"))
            s.add(RcaDraft(id=rid + "-d", review_id=rid, match_tier=1,
                           l1=("Operations Issue" if i % 2 else "Product Issue"),
                           sent_at=mid, booking={"id": f"B{i}"}))
        s.commit()
    finally:
        s.close()
    plain = client.get("/api/reports/daily/preview").json()["text"]
    withl1 = client.get("/api/reports/daily/preview?sections=l1").json()["text"]
    assert "L1 category — received in the window" not in plain
    assert "L1 category — received in the window" in withl1
    # both l1 buckets present and summing to the 4 received
    assert "• Operations Issue — 2" in withl1 and "• Product Issue — 2" in withl1
