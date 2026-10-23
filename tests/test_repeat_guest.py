"""Repeat-reviewer detection: key = guest EMAIL, Zendesk ACCOUNT id (requester_id)
or confirmed BOOKING id. Never a name, never the PII hash. The matcher, the
capture (guest_identity_from_tickets + stamp_identity), and the endpoint payload
are driven with fixtures; no BigQuery / Zendesk / network.

The client tile in client/index.html has no JS test harness here; it only
renders the states this payload carries (CLAUDE.md rule 2 exception).
"""
import asyncio
from datetime import datetime
from types import SimpleNamespace as NS

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from server.services.repeat_guest import (
    find_repeats, guest_key, stamp_booking, stamp_identity)
from server.services.zendesk import guest_identity_from_tickets

HASH_A = "ab24TSVenneb4T3CkHFUFaGMxQ9ZpLm2"
HASH_B = "zz99QQrstuvwx1234567890ABCDEFGhij"


def row(i, bid=None, email=None, rid=None, day=1):
    return {"id": i, "confirmed_bid": bid, "guest_email": email,
            "zendesk_requester_id": rid, "received_at": datetime(2026, 1, day)}


# -- matcher ------------------------------------------------------------------

def test_same_email_case_insensitive_is_a_repeat_ranked_self_excluded():
    me = row("me", "1", "Jo@X.com", day=20)
    others = [row("late", "3", "jo@x.COM", day=10), row("me", "1", "jo@x.com", day=20),
              row("early", "2", " JO@x.com ", day=2), row("other", "9", "z@z.z", day=5)]
    res = find_repeats(me, others)
    assert res["state"] == "found"
    assert [m["id"] for m in res["matches"]] == ["early", "late"]
    assert res["ordinal"] == 3
    assert all(m["matched_on"] == ["email"] for m in res["matches"])


def test_different_email_and_bids_is_no_match():
    res = find_repeats(row("me", "1", "a@x.com"), [row("o", "2", "b@x.com")])
    assert res["state"] == "no_match" and res["matches"] == []


def test_same_requester_id_matches_even_if_emails_differ_or_blank():
    me = row("me", "1", "a@x.com", rid="777")
    res = find_repeats(me, [row("o1", "2", "b@x.com", "777", 2),
                            row("o2", "3", None, "777", 3)])
    assert [m["id"] for m in res["matches"]] == ["o1", "o2"]
    assert all(m["matched_on"] == ["requester_id"] for m in res["matches"])


def test_same_booking_id_matches_without_any_zendesk_identity():
    res = find_repeats(row("me", "77"), [row("o", "77", day=3)])
    assert res["state"] == "found" and res["matches"][0]["matched_on"] == ["booking"]
    assert res["matches"][0]["carry_forward_bid"] == "77"


def test_multiple_keys_reported():
    res = find_repeats(row("me", "1", "a@x.com", "9"),
                       [row("o", "1", "a@x.com", "9", 3)])
    assert sorted(res["matches"][0]["matched_on"]) == ["booking", "email", "requester_id"]


def test_review_with_no_key_makes_no_claim_and_is_never_anyones_repeat():
    empty = row("me")
    assert find_repeats(empty, [row("o", "2", "a@x.com", "5")]) == {
        "state": "not_matched", "matches": [], "ordinal": None,
        "has_key": False, "key_kinds": []}
    # and a keyless OTHER never matches a keyed me, even if both are blank
    res = find_repeats(row("me", "1", "a@x.com"), [row("ghost"), row("g2", None, "", "")])
    assert res["matches"] == []


def test_blank_strings_and_non_emails_are_not_keys():
    assert guest_key({"guest_email": "  ", "zendesk_requester_id": "",
                      "confirmed_bid": None}) == {}
    assert guest_key({"guest_email": "not-an-address"}) == {}
    # two blanks must never "share" an empty value
    assert find_repeats(row("me", "1", ""), [row("o", "2", "")])["matches"] == []


def test_pii_hash_or_name_is_never_a_key():
    """Two different guests whose bookings both carry a hashed primary_guest_name
    (even the SAME hash) must not match: the hash is not an input at all."""
    me = {**row("me", "1"), "primary_guest_name": HASH_A, "guest_name_hash": HASH_A,
          "guest_key": "hash:" + HASH_A}
    o = {**row("o", "2", day=3), "primary_guest_name": HASH_A,
         "guest_name_hash": HASH_A, "guest_key": "hash:" + HASH_A}
    assert find_repeats(me, [o])["matches"] == []
    assert guest_key(me) == {"bid": "1"}


def test_states_are_distinct():
    not_matched = find_repeats(row("me"), [])["state"]
    booking_only = find_repeats(row("me", "1"), [row("o", "2")])["state"]
    no_match = find_repeats(row("me", "1", "a@x.com"), [row("o", "2")])["state"]
    found = find_repeats(row("me", "1"), [row("o", "1")])["state"]
    assert len({not_matched, booking_only, no_match, found}) == 4
    assert (not_matched, booking_only, no_match, found) == (
        "not_matched", "booking_only", "no_match", "found")


def test_undated_matches_sort_last_and_do_not_count_toward_ordinal():
    me = row("me", "1", "a@x.com", day=5)
    undated = {**row("u", "2", "a@x.com"), "received_at": None}
    res = find_repeats(me, [undated, row("d", "3", "a@x.com", day=1)])
    assert [m["id"] for m in res["matches"]] == ["d", "u"] and res["ordinal"] == 2


# -- capture ------------------------------------------------------------------

def _ticket(rid="555", email="Guest@Example.com"):
    return NS(id=1, requester_id=rid, requester=NS(email=email), via=NS(email=""))


def _no_field(t):
    return {"guest_email": ""}


def test_capture_reads_custom_field_email_lowercased_and_requester_id():
    got = guest_identity_from_tickets(
        [_ticket()], signals=lambda t: {"guest_email": " Field@Mail.COM "})
    assert got == {"guest_email": "field@mail.com", "requester_id": "555", "reason": ""}


def test_capture_falls_back_to_requester_email():
    got = guest_identity_from_tickets([_ticket()], signals=_no_field)
    assert got["guest_email"] == "guest@example.com" and got["requester_id"] == "555"


def test_capture_no_ticket_vs_blank_email_have_distinct_reasons():
    none = guest_identity_from_tickets([])
    blank = guest_identity_from_tickets([_ticket(email="")], signals=_no_field)
    assert none["guest_email"] == "" and "no Zendesk ticket" in none["reason"]
    assert blank["guest_email"] == "" and blank["requester_id"] == "555"
    assert "blank" in blank["reason"] and blank["reason"] != none["reason"]


def _draft(booking=None, cand=False):
    from server.db import RcaDraft
    return RcaDraft(id="d", review_id="r", booking=booking, candidate_state=cand)


def test_stamp_identity_stores_email_requester_and_bid_on_the_draft():
    d = _draft({"id": "100"})
    ident = guest_identity_from_tickets([_ticket()], signals=_no_field)
    stamp_identity(d, ident)
    assert (d.confirmed_bid, d.guest_email, d.zendesk_requester_id) == (
        "100", "guest@example.com", "555")
    assert d.guest_identity_reason == ""


def test_stamp_identity_records_why_each_key_is_absent():
    reasons = set()
    d = _draft({"id": "100"})
    stamp_identity(d, guest_identity_from_tickets([]))
    assert d.guest_email is None and d.confirmed_bid == "100"
    reasons.add(d.guest_identity_reason)
    stamp_identity(d, None, zd_error=RuntimeError("429"))
    assert "failed" in d.guest_identity_reason and d.guest_email is None
    reasons.add(d.guest_identity_reason)
    stamp_identity(d, None, zd_searched=False)
    reasons.add(d.guest_identity_reason)
    d2 = _draft(None)
    stamp_identity(d2, guest_identity_from_tickets([_ticket()], signals=_no_field))
    assert d2.confirmed_bid is None and d2.guest_email is None   # unconfirmed: no key
    reasons.add(d2.guest_identity_reason)
    assert len(reasons) == 4 and "" not in reasons


@pytest.mark.parametrize("booking,cand", [
    ({"id": "1"}, True), ({"id": "1", "_unverified": True}, False),
    ({"id": "1", "details_lookup": "failed"}, False), (None, False)])
def test_unconfirmed_booking_never_keeps_zendesk_identity(booking, cand):
    d = _draft(booking, cand)
    stamp_identity(d, {"guest_email": "a@b.c", "requester_id": "9", "reason": ""})
    assert (d.confirmed_bid, d.guest_email, d.zendesk_requester_id) == (None, None, None)


def test_stamp_booking_clears_stale_zendesk_identity():
    d = _draft({"id": "200"})
    d.guest_email, d.zendesk_requester_id = "old@x.com", "1"
    stamp_booking(d)
    assert d.confirmed_bid == "200"
    assert d.guest_email is None and d.zendesk_requester_id is None
    assert d.guest_identity_reason


# -- endpoint payload against a real (sqlite) schema ---------------------------

@pytest.fixture()
def session():
    from server.db import Base
    eng = create_engine("sqlite://")
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng)()
    yield s
    s.close()


def _seed(s, rid, bid=None, email=None, rid_z=None, day=1, booking_extra=None):
    from server.db import Review, RcaDraft
    s.add(Review(id=rid, slack_ts=rid, rating=1, received_at=datetime(2026, 2, day)))
    s.add(RcaDraft(id="d_" + rid, review_id=rid, confirmed_bid=bid,
                   guest_email=email, zendesk_requester_id=rid_z,
                   booking=booking_extra))
    s.commit()


def test_endpoint_end_to_end_all_three_keys(session):
    from server.api import repeat_guest_payload
    _seed(session, "r1", "100", "a@x.com", "9", 1)
    _seed(session, "r2", "200", "A@X.com", None, 9)       # same email
    _seed(session, "r3", "300", None, "9", 5)             # same account
    _seed(session, "r4", "100", None, None, 6)            # same booking
    _seed(session, "r5", "500", "z@z.com", "8", 7)        # someone else
    _seed(session, "r6", None, None, None, 8)             # no key at all
    p = repeat_guest_payload(session, "r1")
    assert p["state"] == "found"
    assert sorted(m["id"] for m in p["matches"]) == ["r2", "r3", "r4"]
    assert p["has_key"] is True
    wire = repr(p)
    assert "a@x.com" not in wire.lower() and "guest_email" not in wire
    assert "zendesk_requester_id" not in wire
    by = {m["id"]: m for m in p["matches"]}
    assert by["r4"]["matched_on"] == ["booking"] and by["r4"]["carry_forward_bid"] == "100"
    assert repeat_guest_payload(session, "r5")["state"] == "no_match"
    assert repeat_guest_payload(session, "r6")["state"] == "not_matched"
    # a keyless review is never anyone's repeat
    assert "r6" not in [m["id"] for m in repeat_guest_payload(session, "r2")["matches"]]


def test_endpoint_booking_only_state_and_hash_never_matches(session):
    from server.api import repeat_guest_payload
    _seed(session, "a", "55", day=1, booking_extra={"id": "55", "primary_guest_name": HASH_A})
    _seed(session, "b", "66", day=2, booking_extra={"id": "66", "primary_guest_name": HASH_A})
    p = repeat_guest_payload(session, "b")
    assert p["state"] == "booking_only" and p["matches"] == []


def test_endpoint_reports_identity_reason(session):
    from server.api import repeat_guest_payload
    from server.db import RcaDraft
    _seed(session, "r", "1")
    session.query(RcaDraft).filter_by(review_id="r").one().guest_identity_reason = \
        "no Zendesk ticket matched this booking"
    session.commit()
    assert "no Zendesk ticket" in repeat_guest_payload(session, "r")["identity_reason"]


def test_endpoint_404s_unknown_review(session):
    from fastapi import HTTPException
    from server.api import repeat_guest_payload
    with pytest.raises(HTTPException):
        repeat_guest_payload(session, "nope")


# -- confirmation routes stamp the bid and drop stale identity ------------------

def _bg():
    from fastapi import BackgroundTasks
    return BackgroundTasks()


def test_select_candidate_stamps_bid_and_clears_stale_email(session, monkeypatch):
    from server import api
    from server.db import Review, RcaDraft
    session.add(Review(id="r", slack_ts="r", rating=1))
    session.add(RcaDraft(id="d", review_id="r", candidate_state=True,
                         candidates_list=[{"id": "900"}], guest_email="old@x.com",
                         zendesk_requester_id="1"))
    session.commit()
    monkeypatch.setattr("server.services.bigquery_patch.verify_bid",
                        lambda bid: {"id": bid, "primary_guest_name": HASH_A})
    monkeypatch.setattr(api.jobs, "enqueue", lambda *a, **k: None)
    asyncio.run(api.select_candidate("r", api.CandidateSelect(bid="900"), _bg(), session))
    d = session.query(RcaDraft).filter_by(review_id="r").one()
    assert d.confirmed_bid == "900" and d.guest_email is None and d.zendesk_requester_id is None


def test_set_booking_id_stamps_the_bid(session, monkeypatch):
    from server import api
    from server.db import Review, RcaDraft
    session.add(Review(id="r", slack_ts="r", rating=1))
    session.commit()
    monkeypatch.setattr(api, "is_live", lambda name: True)
    monkeypatch.setattr("server.services.bigquery_patch.verify_bid",
                        lambda bid: {"id": bid, "primary_guest_name": HASH_B})
    monkeypatch.setattr(api.jobs, "enqueue", lambda *a, **k: None)
    asyncio.run(api.set_booking_id("r", api.BookingIdSet(bid="777"), _bg(), session))
    assert session.query(RcaDraft).filter_by(review_id="r").one().confirmed_bid == "777"


def test_mark_untraceable_clears_every_key(session):
    from server import api
    from server.db import RcaDraft
    _seed(session, "r", "1", "a@x.com", "9", booking_extra={"id": "1"})
    api.mark_untraceable("r", session)
    d = session.query(RcaDraft).filter_by(review_id="r").one()
    assert (d.confirmed_bid, d.guest_email, d.zendesk_requester_id) == (None, None, None)
    assert api.repeat_guest_payload(session, "r")["state"] == "not_matched"
