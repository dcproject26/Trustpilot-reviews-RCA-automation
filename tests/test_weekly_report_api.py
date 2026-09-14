"""The weekly-digest endpoints: options, preview (read-only), and manual send.
Driven through the real app + a real-schema DB (the `client` fixture), with Slack
posting stubbed so no message leaves. Mirrors test_daily_report_api.py."""
import server.config as cfg
import server.services.slack as slack


def test_options_lists_completed_weeks_and_channel(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    r = client.get("/api/reports/weekly/options")
    assert r.status_code == 200
    data = r.json()
    assert data["channel"] == "C045KG5AJF5"
    assert len(data["weeks"]) == 8
    # Newest first, and each carries the weeks_ago the preview/send take.
    assert data["weeks"][0]["weeks_ago"] == 0
    assert "–" in data["weeks"][0]["label"]


def test_preview_returns_text_and_selected_week(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    r = client.get("/api/reports/weekly/preview?weeks_ago=1")
    assert r.status_code == 200
    data = r.json()
    assert data["channel"] == "C045KG5AJF5"
    assert data["weeks_ago"] == 1
    assert "*ORM Weekly —" in data["text"]


def test_preview_rejects_a_silly_week(client):
    # Out-of-range weeks_ago is a clean 422, not a crash.
    assert client.get("/api/reports/weekly/preview?weeks_ago=999").status_code == 422


def test_send_without_channel_is_a_clear_400(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "")
    r = client.post("/api/reports/weekly/send", json={"text": "hi"})
    assert r.status_code == 400
    assert "SLACK_CHANNEL_DAILY" in r.json()["detail"]


def test_send_posts_the_edited_text(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    sent = {}
    def fake_post(channel, text):
        sent["channel"], sent["text"] = channel, text
        return "ts_w1"
    monkeypatch.setattr(slack, "post_to_channel", fake_post)
    r = client.post("/api/reports/weekly/send",
                    json={"text": "EDITED WEEKLY", "weeks_ago": 2})
    assert r.status_code == 200
    assert r.json() == {"ok": True, "ts": "ts_w1", "channel": "C045KG5AJF5"}
    assert sent == {"channel": "C045KG5AJF5", "text": "EDITED WEEKLY"}


def test_send_blank_text_rebuilds_the_selected_week(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    sent = {}
    monkeypatch.setattr(slack, "post_to_channel",
                        lambda ch, txt: sent.setdefault("text", txt) or "ts_w9")
    r = client.post("/api/reports/weekly/send", json={"text": "  ", "weeks_ago": 0})
    assert r.status_code == 200
    assert "*ORM Weekly —" in sent["text"]


def test_send_reports_why_slack_refused(client, monkeypatch):
    monkeypatch.setattr(cfg, "SLACK_CHANNEL_DAILY", "C045KG5AJF5")
    monkeypatch.setattr(slack, "post_to_channel", lambda ch, txt: None)
    monkeypatch.setattr(slack, "last_post_failure", {"why": "bot not in channel"})
    r = client.post("/api/reports/weekly/send", json={"text": "x"})
    assert r.status_code == 502
    assert "bot not in channel" in r.json()["detail"]
