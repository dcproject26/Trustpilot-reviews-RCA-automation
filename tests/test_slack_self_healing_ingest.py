"""sync_channel_to_db's self-healing window — the fix for "up to date" over a
real gap.

The button (and the diagnostic tool with no --hours) used to check a fixed
72h. A webhook outage measured at ~14 days wide sat entirely outside that
window: every check inside it said "up to date" truthfully, and nothing ever
looked far enough back to find the other 41 reviews sitting in Slack. This
sizes the window from the gap since the newest review already in the DB
instead, capped so a very old or empty DB does not scan forever, and paginates
so a wide window is not silently truncated at one page of history.
"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import server.services.slack as slk
from server.db import Review


def _seed_review(live_db, slack_ts: str):
    s = live_db.SessionLocal()
    try:
        s.add(Review(id=f"tp_{slack_ts.replace('.', '_')}", slack_ts=slack_ts,
                     slack_channel="C_ORM", rating=1, language="en",
                     body_original="x", status="new"))
        s.commit()
    finally:
        s.close()


class _Client:
    """Records every conversations_history call; paginates via `pages`."""
    def __init__(self, pages):
        self.pages = list(pages)     # list of message-lists, oldest call first
        self.calls = []

    def conversations_history(self, **kw):
        self.calls.append(kw)
        i = len(self.calls) - 1
        msgs = self.pages[i] if i < len(self.pages) else []
        cursor = f"cursor{i+1}" if i + 1 < len(self.pages) else None
        out = {"messages": msgs}
        if cursor:
            out["response_metadata"] = {"next_cursor": cursor}
        return out


@pytest.fixture(autouse=True)
def _stub_parse(monkeypatch):
    """Every test in this file drives windowing/pagination, not parsing —
    match every message as a fresh Trustpilot review."""
    monkeypatch.setattr(slk, "is_trustpilot_message", lambda ev: True)
    monkeypatch.setattr(slk, "parse_review", lambda ev: {
        "slack_ts": ev["ts"], "slack_channel": "C_ORM", "rating": 1,
        "language": "en", "author": "A", "body_original": ev.get("text", ""),
        "reference_number": None, "published_at": None,
        "published_at_source": ""})


def _run(live_db, monkeypatch, client, hours=None, max_lookback_hours=24 * 30,
         channel="C_ORM"):
    monkeypatch.setattr(slk, "_bot", client)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", channel)
    s = live_db.SessionLocal()
    try:
        return asyncio.run(slk.sync_channel_to_db(
            s, hours=hours, max_lookback_hours=max_lookback_hours))
    finally:
        s.close()


# ── window sizing ────────────────────────────────────────────────────────────

def test_no_reviews_yet_uses_the_full_cap(live_db, monkeypatch):
    client = _Client([[]])
    r = _run(live_db, monkeypatch, client, max_lookback_hours=100)
    assert r["ok"] and r["window_hours"] == 100
    assert "no reviews ingested" in r["window_reason"]


def test_the_window_is_sized_from_the_gap_since_the_last_review(live_db, monkeypatch):
    now = datetime.now(timezone.utc)
    ten_days_ago = (now - timedelta(days=10)).timestamp()
    _seed_review(live_db, f"{ten_days_ago:.6f}")
    client = _Client([[]])
    r = _run(live_db, monkeypatch, client, max_lookback_hours=24 * 30)
    # ~240h gap + 1h margin, not the old fixed 72h.
    assert 240 <= r["window_hours"] <= 242, r
    assert "since the last ingested review" in r["window_reason"]


def test_a_wide_gap_is_capped_not_left_unbounded(live_db, monkeypatch):
    now = datetime.now(timezone.utc)
    year_ago = (now - timedelta(days=365)).timestamp()
    _seed_review(live_db, f"{year_ago:.6f}")
    client = _Client([[]])
    r = _run(live_db, monkeypatch, client, max_lookback_hours=24 * 30)
    assert r["window_hours"] == 24 * 30, \
        "a year-old gap must be capped at max_lookback_hours, not scanned in full"


def test_explicit_hours_is_used_as_is_and_never_queries_the_gap(live_db, monkeypatch):
    """This is the compatibility guarantee for callers with a stub DB that has
    no order_by (test_runs_that_stop.py's _FakeDB, the diagnostic tool's
    --hours): an explicit window must never trigger the gap lookup."""
    class _NoOrderByDB:
        class _Q:
            def filter(self, *a): return self
            def first(self): return None
        def query(self, *a): return self._Q()
        def add(self, *a): pass
        def commit(self): pass

    client = _Client([[]])
    monkeypatch.setattr(slk, "_bot", client)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "C_ORM")
    r = asyncio.run(slk.sync_channel_to_db(_NoOrderByDB(), hours=5))
    assert r["ok"] and r["window_hours"] == 5.0
    assert r["window_reason"] == "5h requested"


def test_a_review_posted_seconds_before_the_check_stays_in_window(live_db, monkeypatch):
    """The +1h margin: a review landing in the same minute as the gap
    computation must not fall just outside the window it produces."""
    now = datetime.now(timezone.utc)
    _seed_review(live_db, f"{(now - timedelta(seconds=5)).timestamp():.6f}")
    client = _Client([[]])
    r = _run(live_db, monkeypatch, client)
    assert r["window_hours"] >= 1.0


# ── pagination ───────────────────────────────────────────────────────────────

def test_multiple_pages_are_all_scanned(live_db, monkeypatch):
    page1 = [{"ts": "1.0", "text": "r1"}]
    page2 = [{"ts": "2.0", "text": "r2"}]
    client = _Client([page1, page2])
    r = _run(live_db, monkeypatch, client)
    assert r["ok"]
    assert r["messages_scanned"] == 2, \
        "the second page was never fetched — a wide window truncates at 200"
    assert r["queued"] == 2
    assert len(client.calls) == 2
    assert "cursor" not in client.calls[0]
    assert client.calls[1]["cursor"] == "cursor1"


def test_pagination_stops_at_the_page_cap(live_db, monkeypatch):
    pages = [[{"ts": f"{i}.0", "text": "r"}] for i in range(10)]
    client = _Client(pages)
    monkeypatch.setattr(slk, "_bot", client)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "C_ORM")
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(slk.sync_channel_to_db(s, hours=1, max_pages=3))
    finally:
        s.close()
    assert len(client.calls) == 3, \
        "max_pages must bound a pathologically long cursor chain"
    assert r["messages_scanned"] == 3


# ── failure modes ────────────────────────────────────────────────────────────

def test_no_client_or_channel_is_not_configured(live_db, monkeypatch):
    monkeypatch.setattr(slk, "_bot", None)
    monkeypatch.setattr(slk, "_user", None)
    monkeypatch.setattr("server.config.SLACK_CHANNEL_ORM", "")
    s = live_db.SessionLocal()
    try:
        r = asyncio.run(slk.sync_channel_to_db(s, hours=1))
    finally:
        s.close()
    assert r == {"ok": False, "error_kind": "not_configured",
                "error": "Slack not configured (needs SLACK_BOT_TOKEN + "
                         "SLACK_CHANNEL_ORM)"}


def test_a_slack_read_failure_is_a_distinct_error_kind(live_db, monkeypatch):
    class _Boom:
        def conversations_history(self, **kw):
            raise RuntimeError("rate limited")
    r = _run(live_db, monkeypatch, _Boom(), hours=1)
    assert r["ok"] is False
    assert r["error_kind"] == "history_failed"
    assert "rate limited" in r["error"]


# ── already-present dedup still works with the new window ───────────────────

def test_already_ingested_reviews_are_skipped_not_re_added(live_db, monkeypatch):
    now = datetime.now(timezone.utc)
    ts = f"{now.timestamp():.6f}"
    _seed_review(live_db, ts)
    client = _Client([[{"ts": ts, "text": "dup"}]])
    r = _run(live_db, monkeypatch, client, hours=1)
    assert r["trustpilot_found"] == 1
    assert r["already_present"] == 1
    assert r["queued"] == 0
