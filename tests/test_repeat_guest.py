"""Repeat-reviewer detection is a POST-CONFIRMATION property of the booking's
guest - never the display name. Pure matcher + key resolution + the endpoint
payload are driven with fixtures (no BigQuery).

The client panel in client/index.html has no JS test harness here; it only
renders the states this payload carries (CLAUDE.md rule 2 exception).
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.services.repeat_guest import (
    find_repeats, guest_key_from_booking, stamp_guest_key)

HASH_A = "ab24TSVenneb4T3CkHFUFaGMxQ9ZpLm2"
HASH_B = "zz99QQrstuvwx1234567890ABCDEFGhij"


def row(i, bid, key=None, day=1):
    return {"id": i, "confirmed_bid": bid, "guest_key": key,
            "received_at": datetime(2026, 1, day)}


# ── key resolution ───────────────────────────────────────────────────────────

def test_hash_is_the_key_when_there_is_no_email():
    r = guest_key_from_booking({"id": "111", "primary_guest_name": HASH_A})
    assert r["guest_key"] == "hash:" + HASH_A and r["confirmed_bid"] == "111"


def test_guest_email_beats_hash_and_is_normalised():
    r = guest_key_from_booking({"id": "111", "primary_guest_name": HASH_A,
                                "guest_email": " Jo@X.COM "})
    assert r["guest_key"] == "email:jo@x.com"
    assert r["guest_name_hash"] == HASH_A


def test_vendor_escalation_email_is_not_a_guest_identity():
    r = guest_key_from_booking({"id": "111", "escalationEmail": "ops@vendor.com",
                                "escalation_email": "ops@vendor.com"})
    assert r["guest_key"] is None and r["guest_email"] is None


def test_plain_display_name_is_not_a_key():
    r = guest_key_from_booking({"id": "111", "primary_guest_name": "John Smith"})
    assert r["guest_key"] is None
    assert r["confirmed_bid"] == "111" and r["reason"]


@pytest.mark.parametrize("booking,cand", [
    ({"primary_guest_name": HASH_A}, False),                 # no id
    ({"id": "1", "primary_guest_name": HASH_A}, True),       # picker active
    ({"id": "1", "primary_guest_name": HASH_A, "_unverified": True}, False),
    ({"id": "1", "primary_guest_name": HASH_A, "details_lookup": "failed"}, False),
    (None, False),
])
def test_unconfirmed_booking_yields_no_key_and_no_bid(booking, cand):
    r = guest_key_from_booking(booking, cand)
    assert r["guest_key"] is None and r["confirmed_bid"] is None and r["reason"]


# ── matching ─────────────────────────────────────────────────────────────────

def test_same_email_detected_ranked_by_date_self_excluded():
    me = row("me", "1", "email:a@b.c", day=20)
    others = [row("late", "3", "email:a@b.c", day=10),
              row("me", "1", "email:a@b.c", day=20),   # self
              row("early", "2", "email:a@b.c", day=2),
              row("other", "9", "email:z@z.z", day=5)]
    res = find_repeats(me, others)
    assert res["state"] == "found"
    assert [m["id"] for m in res["matches"]] == ["early", "late"]
    assert res["ordinal"] == 3          # two earlier + this one
    assert all(m["matched_on"] == "guest" for m in res["matches"])


def test_same_hash_without_email_still_detected():
    me = row("me", "1", "hash:" + HASH_A)
    res = find_repeats(me, [row("o", "2", "hash:" + HASH_A, day=3)])
    assert [m["id"] for m in res["matches"]] == ["o"]


def test_same_booking_id_detected_even_without_any_key():
    me = row("me", "77", None)
    res = find_repeats(me, [row("o", "77", None, day=3)])
    assert res["state"] == "found" and res["matches"][0]["matched_on"] == "booking"
    assert res["has_guest_key"] is False


def test_unconfirmed_review_makes_no_claim():
    me = {"id": "me", "confirmed_bid": None, "guest_key": "hash:" + HASH_A,
          "received_at": None}
    res = find_repeats(me, [row("o", "2", "hash:" + HASH_A)])
    assert res["state"] == "not_confirmed" and res["matches"] == []


def test_unconfirmed_other_is_never_returned():
    me = row("me", "1", "hash:" + HASH_A)
    ghost = {"id": "g", "confirmed_bid": None, "guest_key": "hash:" + HASH_A,
             "received_at": datetime(2026, 1, 1)}
    res = find_repeats(me, [ghost])
    assert res["matches"] == [] and res["state"] == "none"


def test_different_guests_do_not_match():
    me = row("me", "1", "hash:" + HASH_A)
    res = find_repeats(me, [row("o", "2", "hash:" + HASH_B)])
    assert res["state"] == "none" and res["matches"] == []


def test_carry_forward_bid_is_surfaced():
    me = row("me", "1", "hash:" + HASH_A)
    res = find_repeats(me, [row("o", "424242", "hash:" + HASH_A)])
    assert res["matches"][0]["carry_forward_bid"] == "424242"


def test_no_guest_key_state_differs_from_no_repeats():
    nokey = find_repeats(row("me", "1", None), [row("o", "2", "hash:" + HASH_A)])
    none = find_repeats(row("me", "1", "hash:" + HASH_A),
                        [row("o", "2", "hash:" + HASH_B)])
    unconf = find_repeats({"id": "me", "confirmed_bid": None, "guest_key": None},
                          [])
    assert {nokey["state"], none["state"], unconf["state"]} == {
        "no_guest_key", "none", "not_confirmed"}


def test_undated_matches_sort_last_and_do_not_count_toward_ordinal():
    me = row("me", "1", "hash:" + HASH_A, day=5)
    undated = {"id": "u", "confirmed_bid": "2", "guest_key": "hash:" + HASH_A,
               "received_at": None}
    res = find_repeats(me, [undated, row("d", "3", "hash:" + HASH_A, day=1)])
    assert [m["id"] for m in res["matches"]] == ["d", "u"]
    assert res["ordinal"] == 2


# ── stamping + endpoint payload against a real (sqlite) schema ───────────────

@pytest.fixture()
def session():
    from server.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _seed(s, rid, booking, day, cand=False):
    from server.db import Review, RcaDraft
    s.add(Review(id=rid, slack_ts=rid, rating=1, received_at=datetime(2026, 2, day)))
    d = RcaDraft(id="d_" + rid, review_id=rid, booking=booking,
                 candidate_state=cand)
    stamp_guest_key(d)
    s.add(d)
    s.commit()


def test_endpoint_payload_end_to_end(session):
    from server.api import repeat_guest_payload
    _seed(session, "r1", {"id": "100", "primary_guest_name": HASH_A}, 1)
    _seed(session, "r2", {"id": "200", "primary_guest_name": HASH_A}, 9)
    _seed(session, "r3", {"id": "300", "primary_guest_name": HASH_B}, 5)
    _seed(session, "r4", None, 6)                       # untraceable
    _seed(session, "r5", {"id": "500", "primary_guest_name": HASH_A}, 7, cand=True)

    p = repeat_guest_payload(session, "r2")
    assert p["state"] == "found" and p["ordinal"] == 2
    assert [m["id"] for m in p["matches"]] == ["r1"]
    assert p["matches"][0]["carry_forward_bid"] == "100"
    assert "guest_key" not in p["matches"][0]          # identity not on the wire

    assert repeat_guest_payload(session, "r3")["state"] == "none"
    assert repeat_guest_payload(session, "r4")["state"] == "not_confirmed"
    assert repeat_guest_payload(session, "r5")["state"] == "not_confirmed"


def test_endpoint_404s_unknown_review(session):
    from fastapi import HTTPException
    from server.api import repeat_guest_payload
    with pytest.raises(HTTPException):
        repeat_guest_payload(session, "nope")


# ── the confirmation routes actually store the key ───────────────────────────

import asyncio


def _bg():
    from fastapi import BackgroundTasks
    return BackgroundTasks()


def test_same_booking_found_without_any_guest_key(session):
    from server.api import repeat_guest_payload
    _seed(session, "a", {"id": "55", "primary_guest_name": "Plain Name"}, 1)
    _seed(session, "b", {"id": "55", "primary_guest_name": "Plain Name"}, 2)
    p = repeat_guest_payload(session, "b")
    assert p["state"] == "found" and p["has_guest_key"] is False
    assert p["matches"][0]["matched_on"] == "booking"


def test_select_candidate_stamps_the_key(session, monkeypatch):
    from server import api
    from server.db import Review, RcaDraft
    session.add(Review(id="r", slack_ts="r", rating=1))
    session.add(RcaDraft(id="d", review_id="r", candidate_state=True,
                         candidates_list=[{"id": "900"}]))
    session.commit()
    monkeypatch.setattr("server.services.bigquery_patch.verify_bid",
                        lambda bid: {"id": bid, "primary_guest_name": HASH_A})
    monkeypatch.setattr(api.jobs, "enqueue", lambda *a, **k: None)
    asyncio.run(api.select_candidate("r", api.CandidateSelect(bid="900"),
                                     _bg(), session))
    d = session.query(RcaDraft).filter_by(review_id="r").one()
    assert d.confirmed_bid == "900" and d.guest_key == "hash:" + HASH_A


def test_set_booking_id_stamps_the_key(session, monkeypatch):
    from server import api
    from server.db import Review, RcaDraft
    session.add(Review(id="r", slack_ts="r", rating=1))
    session.commit()
    monkeypatch.setattr(api, "is_live", lambda name: True)
    monkeypatch.setattr("server.services.bigquery_patch.verify_bid",
                        lambda bid: {"id": bid, "primary_guest_name": HASH_B})
    monkeypatch.setattr(api.jobs, "enqueue", lambda *a, **k: None)
    asyncio.run(api.set_booking_id("r", api.BookingIdSet(bid="777"),
                                   _bg(), session))
    d = session.query(RcaDraft).filter_by(review_id="r").one()
    assert d.confirmed_bid == "777" and d.guest_key == "hash:" + HASH_B


def test_mark_untraceable_clears_a_previous_key(session):
    from server import api
    from server.db import RcaDraft
    _seed(session, "r", {"id": "1", "primary_guest_name": HASH_A}, 1)
    api.mark_untraceable("r", session)
    d = session.query(RcaDraft).filter_by(review_id="r").one()
    assert d.guest_key is None and d.confirmed_bid is None
    assert api.repeat_guest_payload(session, "r")["state"] == "not_confirmed"
