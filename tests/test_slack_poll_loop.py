"""The background reconcile cycle (main._slack_poll_once) — the guarantee half
of "update reviews as and when they come". The webhook is the fast path; this
runs the SAME ingest cascade (slack.sync_channel_to_db) on a timer, so a
webhook outage of any length is recovered without anyone clicking Refresh.

The loop itself (`while True: ... await asyncio.sleep(180)`) is not driven
here — it never returns, so a test can only run it with a hard timeout, which
proves the sleep happened and nothing about the ingest. _slack_poll_once is the
one cycle's worth of real work, factored out so it can be called directly and
its actual behaviour (does it queue?) is what gets asserted, per CLAUDE.md
rule 2 — the loop wrapper is then a thin, low-risk shell around a function this
file exercises directly.
"""
import asyncio

import server.services.slack as slk
from server.main import _slack_poll_once


class _Client:
    def __init__(self, msgs):
        self.msgs = msgs

    def conversations_history(self, **kw):
        return {"messages": self.msgs}


def _stub_slack(monkeypatch, msgs, channel="C_ORM"):
    monkeypatch.setattr(slk, "_bot", _Client(msgs))
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", channel)
    monkeypatch.setattr(slk, "is_trustpilot_message", lambda ev: True)
    monkeypatch.setattr(slk, "parse_review", lambda ev: {
        "slack_ts": ev["ts"], "slack_channel": channel, "rating": 1,
        "language": "en", "author": "A", "body_original": ev.get("text", ""),
        "reference_number": None, "published_at": None,
        "published_at_source": ""})


def test_a_poll_cycle_queues_a_missed_review(live_db, monkeypatch):
    _stub_slack(monkeypatch, [{"ts": "1700000000.0", "text": "1 star"}])
    enq = []
    import server.main as m
    monkeypatch.setattr(m, "_db", live_db, raising=False)
    monkeypatch.setattr("server.jobs.enqueue",
                        lambda rid, reason="", force_candidates=False: enq.append((rid, reason)))
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(_slack_poll_once(s))
    finally:
        s.close()
    assert r["ok"] and r["queued"] == 1
    assert enq == [("tp_1700000000_0", "slack-poll")], \
        "the poll cycle found a new review but never queued its run"


def test_a_quiet_cycle_queues_nothing(live_db, monkeypatch):
    _stub_slack(monkeypatch, [])
    enq = []
    monkeypatch.setattr("server.jobs.enqueue",
                        lambda rid, reason="", force_candidates=False: enq.append(rid))
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(_slack_poll_once(s))
    finally:
        s.close()
    assert r["ok"] and r["queued"] == 0
    assert enq == []


def test_an_unconfigured_slack_is_reported_not_raised(live_db, monkeypatch):
    """The loop wrapping this must survive a container with no Slack tokens
    set (a dev/mock environment) rather than crash its background task."""
    monkeypatch.setattr(slk, "_bot", None)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "")
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(_slack_poll_once(s))
    finally:
        s.close()
    assert r["ok"] is False
    assert r["error_kind"] == "not_configured"
