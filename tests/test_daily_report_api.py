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
