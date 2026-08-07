"""The card told the reader to do a lookup the run had already done.

Booking 32885089's `primary_guest_name` is a PII hash, so the Primary guest
row read:

    — the warehouse stores this as a hash — check the Zendesk ticket

`guest_name_for_bid()` IS called (pipeline.py) precisely because the warehouse
holds nothing usable. It returns the readable name off the ticket, the name
scores the match through `gate_name_check`, and then it was dropped: nothing
wrote it onto the booking, and `_first_guest_name` in api.py reads only the
warehouse spellings. So the fetch happened, the answer was used once, and the
card asked a person to fetch it again by hand.

A lookup that runs, succeeds, and stores nothing is indistinguishable from one
that never ran — the card renders the same sentence either way.
"""
import pytest

from server.api import _looks_like_hash


HASH = "jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8="


def _first_guest_name(*cands):
    """The api helper, which is defined inside the endpoint's closure."""
    from server.names import is_internal_booking_name
    for c in cands:
        c = (c or "").strip()
        if c and not _looks_like_hash(c) and not is_internal_booking_name(c):
            return c
    return ""


def test_the_fixture_is_the_reported_case():
    assert _looks_like_hash(HASH), "the fixture no longer exercises a hash"


def test_the_zendesk_name_is_read_when_the_warehouse_holds_a_hash():
    got = _first_guest_name(None, None, HASH, None, "Bénédicte Depois", None)
    assert got == "Bénédicte Depois", got


def test_the_warehouse_still_wins_when_it_is_readable():
    """The booking's own record is authoritative wherever it can be read; the
    ticket is the fallback, not the preference."""
    got = _first_guest_name(None, None, "Mariana Compos", None,
                            "Someone Else", None)
    assert got == "Mariana Compos", got


def test_a_hashed_zendesk_copy_is_not_shown_either():
    assert _first_guest_name(None, None, HASH, None, HASH, None) == ""


def test_an_internal_label_is_not_shown_as_a_guest():
    """A desk-made booking under "Customer Ops Lead" is not a guest name, and
    showing it invites a comparison against the reviewer that proves nothing."""
    assert _first_guest_name(None, None, HASH, None,
                             "Customer Ops Lead", None) == ""


def test_the_card_reads_the_stored_field():
    """WIRING, and the half that scoring the name cannot show. `_bk` is the
    booking dict; a key the endpoint never reads is a name fetched and thrown
    away, which is what happened."""
    src = open("server/api.py", encoding="utf-8").read()
    i = src.index("guest_name = _first_guest_name(")
    assert '_bk.get("zendesk_guest_name")' in src[i:src.index(")", src.index("zendesk_requester_name", i))], \
        "the card does not read the Zendesk name the run fetched"


def test_the_pipeline_stores_what_it_fetched():
    """The other half. `guest_name_for_bid` returning a name is useless if the
    run ends without writing it down — and the trail line that says the name
    "came from Zendesk" renders either way, so nothing looked wrong."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert 'bq_row["zendesk_guest_name"] = _zdn' in src, \
        "the Zendesk name is fetched, scored, and dropped again"
