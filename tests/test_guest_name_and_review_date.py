"""The guest name field, and the review's own date.

TWO DEFECTS THAT SURVIVED SEVERAL CORRECTIONS EACH, both for the same reason:
the fixes went into the heuristics and never checked the KEY, or went into one
of two contradicting rules and left the other.
"""
import pytest
from datetime import datetime

from scripts.backfill_received_at import classify, slack_time


# ── the guest name: the warehouse writes primary_guest_name ────────────────

def _draft_with(live_db, booking, rid="tp_gn"):
    """A draft carrying one booking dict, read back through the API."""
    from fastapi.testclient import TestClient
    from server.main import app
    from server.db import get_session
    s = live_db.SessionLocal()
    s.add(live_db.Review(id=rid, rating=1, author="A", body_original="b",
                         status="draft"))
    s.add(live_db.RcaDraft(id=f"d_{rid}", review_id=rid, booking=booking))
    s.commit(); s.close()
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    try:
        with TestClient(app) as c:
            r = c.get(f"/api/reviews/{rid}")
            assert r.status_code == 200, r.text
            return r.json()["draft"]
    finally:
        app.dependency_overrides.clear()


def test_the_name_is_read_from_the_field_the_warehouse_writes(live_db):
    """DRIVEN, not a source assertion — the first version of this test read
    the function's own text and passed against a build where the argument had
    been replaced with None, because the field name was still in the comment
    beside it. That is the spelling check CLAUDE.md forbids, written by me in
    the commit that fixed the bug.

    `verify_bid` builds the booking with `primary_guest_name` and nothing
    else. `_first_guest_name` read every other spelling, so a booking WITH a
    name reported that none was found."""
    d = _draft_with(live_db, {"id": "1", "primary_guest_name": "Angus Rorison"})
    assert d["guest_name"] == "Angus Rorison", d.get("guest_name")
    assert not d["guest_name_note"], d["guest_name_note"]


def test_the_hash_branch_looks_at_the_same_field(live_db):
    """If the hash check reads a different set of keys from the picker, a hash
    in `primary_guest_name` produces "no Zendesk ticket was matched" — a true
    sentence about the wrong thing, and the reader opens Zendesk for nothing."""
    d = _draft_with(live_db, {"id": "1",
                              "primary_guest_name": "a3f9c2e7b1d84f60a9c2"},
                    rid="tp_gn_hash")
    assert d["guest_name"] == "", d["guest_name"]
    assert "hash" in d["guest_name_note"], d["guest_name_note"]


def test_the_client_no_longer_re_derives_the_name():
    """CLIENT-SIDE JAVASCRIPT, which has no test harness here — a NEGATIVE
    source assertion, the one form unreachability cannot defeat.

    The card had its own guess at what a warehouse hash looks like
    (`length <= 20 || contains a space`) beside three server-side copies of
    the same rule. They disagreed, which is why the name looked intermittent
    rather than simply absent."""
    src = open("client/index.html").read()
    assert "rawGuest.length <= 20" not in src, \
        "the client's own hash heuristic is back"
    assert "draft.guest_name" in src, \
        "the client must read the server's resolved name"


# ── the review's own date ──────────────────────────────────────────────────

def test_the_prompt_no_longer_orders_the_review_last():
    """NEGATIVE source assertion on the prompt text. Rule 6 said "Review
    posted last" while the bookend rule twenty lines up said "NOT necessarily
    last". A review published BEFORE a later CE reply then rendered after it."""
    src = open("server/prompts.py").read()
    assert "events as given, Review posted last" not in src


def test_the_actor_rule_no_longer_hands_back_a_closed_vocabulary():
    """NEGATIVE source assertion. The descriptive-labels rule was cancelled
    twenty lines later by an actor rule prefaced "outranks all of this" that
    prescribed the exact six nouns the table forbids."""
    src = open("server/prompts.py").read()
    assert 'actor "co"      -> "CE response"' not in src
    assert 'actor "sp"      -> "SP response"' not in src


# ── the backfill, driven ───────────────────────────────────────────────────

def test_a_slack_ts_outside_any_plausible_range_is_not_a_date():
    """Treating one as a date puts a review in 1970, which renders as data
    rather than as the parse failure it is."""
    for bad in ("0", "", None, "not-a-ts", "99999999999"):
        assert slack_time(bad) is None, bad


def test_the_ingest_moment_is_moved_to_the_relay_time():
    ts = "1753539960.0"                       # 26 Jul 2025
    relay = slack_time(ts)
    late = relay.replace(year=relay.year + 1)  # a much later "ingest" stamp
    action, why = classify(late, ts)
    assert action == "set", (action, why)
    assert "ingest moment" in why


def test_a_date_EARLIER_than_the_relay_is_left_alone():
    """Earlier than the relay is what a real publish date looks like — the
    review existed before Slack carried it. Moving it forward would replace
    the better fact with the worse one."""
    ts = "1753539960.0"
    early = slack_time(ts).replace(day=1)
    action, why = classify(early, ts)
    assert action == "keep", (action, why)
    assert "publish date" in why


def test_a_row_already_on_the_relay_time_is_not_rewritten():
    ts = "1753539960.0"
    action, _ = classify(slack_time(ts), ts)
    assert action == "keep"


def test_a_row_with_no_usable_slack_ts_is_reported_not_silently_left():
    """"We could not move this" and "this needed no move" are different facts
    and must not both render as a row that did not change."""
    action, why = classify(datetime(2030, 1, 1), "garbage")
    assert action == "skip", action
    assert "no better value" in why
