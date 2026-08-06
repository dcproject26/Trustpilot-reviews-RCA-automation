"""The review's date is when the review came in, at every creation site.

The inbox showed two different reviews both stamped "05 Aug, 07:47" — which
is what a whole batch stamped in one second looks like.

THE COLUMN HAD A DEFAULT. `received_at = Column(DateTime,
default=datetime.utcnow)` does not leave the column empty when a creation site
forgets it: it INVENTS a date, the moment this process happened to run, and
nothing downstream can tell an invented one from a real one.

The batch importer had guarded against exactly this since it was written. The
LIVE WEBHOOK — the path every real review arrives on — passed no received_at
at all and got the default. Two paths, one fixed, and the wrong one was the
one carrying the traffic.

The default is gone. Every site states the date, and a site that forgets gets
NULL, which renders as "no date recorded" — visibly missing rather than
quietly wrong.
"""
import ast
import inspect
import pathlib

import pytest


def _review_creations():
    """Every `Review(...)` construction in the server, with its keywords."""
    out = []
    root = pathlib.Path(__file__).resolve().parents[1] / "server"
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for n in ast.walk(tree):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                    and n.func.id == "Review"):
                out.append((path.name, n.lineno,
                            {k.arg for k in n.keywords if k.arg}))
    return out


def test_the_sweep_finds_the_creation_sites_at_all():
    """NOT BUILT guard: an empty list would make the check below pass by
    inspecting nothing."""
    got = _review_creations()
    assert len(got) >= 3, got


def test_every_creation_site_states_the_review_date():
    """The one that forgot was the live webhook, and it was invisible because
    the default filled it in."""
    missing = [f"{f}:{ln}" for f, ln, kw in _review_creations()
               if "received_at" not in kw]
    assert not missing, (
        f"these create a Review without a received_at: {missing}. With no "
        f"column default they will store NULL; with one they would silently "
        f"store the moment this process ran.")


def test_the_column_no_longer_invents_a_date():
    """NEGATIVE source assertion, permitted by CLAUDE.md. A default here is
    not a convenience — it is a fact nobody established, indistinguishable
    from one that was."""
    from server import db
    src = inspect.getsource(db)
    assert "received_at      = Column(DateTime, default=datetime.utcnow)" not in src, (
        "the review date has a column default again — a creation site that "
        "forgets it will silently stamp the ingest moment")


def test_the_review_column_is_nullable_so_a_gap_is_visible():
    from server.db import Review
    assert Review.__table__.c.received_at.nullable is True
    assert Review.__table__.c.received_at.default is None


# ── the webhook, which is the path that was wrong ──────────────────────────

def test_the_webhook_derives_the_date_from_the_message_not_from_now():
    """slack_ts is the message's own timestamp and is already in hand — the
    review id is built from it."""
    from server import webhook
    src = inspect.getsource(webhook)
    assert "_received_at_from(" in src, (
        "the live webhook no longer derives the review date from the Slack "
        "message; it is back to whatever the column does")
    assert 'received_at      = _at' in src


def test_the_webhook_passes_the_publish_date_when_the_payload_has_one():
    """Trustpilot's own timestamp beats the moment Slack relayed it."""
    from server import webhook
    src = inspect.getsource(webhook)
    assert 'parsed.get("published_at")' in src, src[:200]


# ── and the derivation itself ──────────────────────────────────────────────

def test_the_publish_date_wins_over_the_slack_arrival_time():
    from datetime import datetime
    from server.api import _received_at_from
    got = _received_at_from("1785931800.0", "rv1",
                            datetime(2026, 8, 2, 9, 15), "attachment_ts")
    assert got == datetime(2026, 8, 2, 9, 15)


def test_the_slack_timestamp_is_used_when_there_is_no_publish_date():
    from datetime import datetime
    from server.api import _received_at_from
    got = _received_at_from("1785931800.0", "rv1", None, "")
    assert isinstance(got, datetime)
    assert got.year == 2026


def test_two_reviews_from_different_messages_get_different_dates():
    """The symptom, stated as a test: a whole batch reading the same minute."""
    from server.api import _received_at_from
    a = _received_at_from("1785931800.0", "a", None, "")
    b = _received_at_from("1785662100.0", "b", None, "")
    assert a != b, (a, b)


def test_a_metric_row_copies_the_reviews_date_rather_than_taking_its_own():
    """ReviewMetric holds a second copy of this fact. Two stores for one fact
    is how the card and the reporting page disagree about the same review."""
    from server import pipeline
    src = inspect.getsource(pipeline)
    assert "m.received_at      = review.received_at" in src, (
        "the metric no longer derives its date from the review")
