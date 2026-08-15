"""/api/version exposes Slack webhook delivery health — and a broken read of it
must never look like a quiet channel.

The bug this guards is not in the count, it is in what a zero MEANS. A dev-repl
count of 0 was reported as "the webhook is broken", when the dev repl is a
database Slack never posts to — rule 1 wearing an environment hat. The endpoint
now lets production answer for itself; these tests pin the one distinction that
makes the answer trustworthy: "we read zero" and "we could not read" come out
different, and "not lately" is not "never".

Driven against the real helper and the real endpoint — no source assertions.
"""
from datetime import datetime, timedelta

import server.api as api


def _seed_event(live_db, event_id, seen_at):
    s = live_db.SessionLocal()
    s.add(live_db.SlackEventSeen(event_id=event_id, seen_at=seen_at))
    s.commit()
    s.close()


def test_counts_recent_deliveries(live_db):
    now = datetime.utcnow()
    _seed_event(live_db, "e1", now - timedelta(hours=1))
    _seed_event(live_db, "e2", now - timedelta(hours=2))
    h = api._webhook_health(window_hours=72)
    assert h["recent_deliveries"] == 2
    assert "error" not in h
    assert h["last_seen_at"] is not None


def test_the_window_bounds_the_count(live_db):
    now = datetime.utcnow()
    _seed_event(live_db, "in", now - timedelta(hours=1))
    _seed_event(live_db, "out", now - timedelta(hours=100))
    assert api._webhook_health(window_hours=72)["recent_deliveries"] == 1


def test_a_genuine_zero_is_zero_not_an_error(live_db):
    h = api._webhook_health(window_hours=72)
    assert h["recent_deliveries"] == 0      # a real, read zero
    assert "error" not in h
    assert h["last_seen_at"] is None        # and nothing has ever arrived


def test_zero_with_an_old_last_seen_is_not_zero_with_none(live_db):
    """count 0 + an OLD last_seen ('not lately') must read differently from
    count 0 + no last_seen ('never') — delivery stopped vs never started."""
    never = api._webhook_health(window_hours=72)
    assert never["recent_deliveries"] == 0 and never["last_seen_at"] is None

    _seed_event(live_db, "old", datetime.utcnow() - timedelta(hours=200))
    stopped = api._webhook_health(window_hours=72)
    assert stopped["recent_deliveries"] == 0        # still nothing in-window
    assert stopped["last_seen_at"] is not None       # but something, long ago
    assert stopped["last_seen_at"] != never["last_seen_at"]


def test_an_unreadable_table_is_not_reported_as_zero(live_db):
    """THE POINT. Drop the table so the read fails; the result must carry an
    error and a NULL count — never the 0 a healthy-but-quiet channel returns.
    'I could not run' and 'I ran and found nothing' are different answers."""
    genuine_zero = api._webhook_health(window_hours=72)
    assert genuine_zero["recent_deliveries"] == 0 and "error" not in genuine_zero

    live_db.SlackEventSeen.__table__.drop(live_db.engine)
    broken = api._webhook_health(window_hours=72)
    assert "error" in broken
    assert broken["recent_deliveries"] is None
    # the two must not be confusable — that is the entire reason this exists
    assert broken["recent_deliveries"] != genuine_zero["recent_deliveries"]


def test_version_endpoint_carries_webhook_health(client, live_db):
    _seed_event(live_db, "e1", datetime.utcnow() - timedelta(minutes=5))
    r = client.get("/api/version")
    assert r.status_code == 200
    wh = r.json()["webhook"]
    assert wh["recent_deliveries"] == 1
    assert "error" not in wh
    assert wh["last_seen_at"] is not None
