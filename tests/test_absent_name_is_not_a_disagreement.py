"""A ticket with no guest name recorded cannot contradict the reviewer.

THE REPORTED CASE. A support ticket for Mariana Campos, found on Zendesk by
hand, carrying the booking id in its BODY. The review named the guest, the
venue and the city. The pipeline filed it Untraceable.

`matches_indicators` opened with:

    if first or last:
        if not name_matches(sig.get("guest_name") or "", first, last):
            return False, used

`sig["guest_name"]` is the ticket's guest-name CUSTOM FIELD. Empty field →
`name_matches("", "Mariana", "Campos")` → False → the ticket is rejected
outright. An ABSENT field treated as a DISAGREEMENT.

The venue check ten lines below is careful about exactly this:

    "A ticket that records NO experience cannot contradict the review's venue
     - it simply has nothing to say. Rejecting on it threw away tickets whose
     requester name matched exactly."

The same is true of the name, and getting it wrong here costs more: the
tickets with an empty guest-name field are the SAME sparse tickets that have
an empty booking-id field — the ones the body-BID fallback was built for. They
were thrown away before that fallback could look at them, found by a
requester search that matched the guest exactly and then rejected for failing
to repeat the name in a field nobody filled in.
"""
import asyncio

import pytest

from server.services.zendesk import matches_indicators, shortlist
import server.services.zendesk as Z

IND = {"experience_or_venue": "Universal", "city_or_country": "Orlando"}


def _sig(guest="", exp="Universal Studios Orlando", city="Orlando"):
    return {"guest_name": guest, "experience": exp, "city": city, "pax": None}


# ── the rule ───────────────────────────────────────────────────────────────

def test_a_ticket_with_no_guest_name_is_not_rejected():
    """The bug, in one assertion."""
    ok, used = matches_indicators(_sig(""), IND, "Mariana", "Campos")
    assert ok is True, "a ticket with no guest name recorded was rejected"


def test_it_does_not_claim_the_name_as_agreement_either():
    """Not rejecting is not the same as agreeing. A ticket carried this far on
    venue and city must not report a name match it never made."""
    _, used = matches_indicators(_sig(""), IND, "Mariana", "Campos")
    assert "name" not in used, used


def test_a_name_that_IS_present_and_disagrees_still_rejects():
    """The guard the original line was written for, unchanged. Loosening this
    would admit strangers' tickets, which is worse than the bug being fixed."""
    ok, _ = matches_indicators(_sig("Someone Else"), IND, "Mariana", "Campos")
    assert ok is False


def test_a_name_that_agrees_is_still_recorded_as_agreement():
    ok, used = matches_indicators(_sig("Mariana Campos"), IND, "Mariana", "Campos")
    assert ok is True
    assert "name" in used, used


def test_an_unnamed_ticket_is_still_rejected_when_the_venue_disagrees():
    """Dropping the name check must not drop the others with it."""
    ok, _ = matches_indicators(_sig("", "Colosseum Tour", "Rome"),
                               IND, "Mariana", "Campos")
    assert ok is False


def test_the_absence_is_recorded_so_the_caller_can_report_it():
    """`name_checked` is what lets the trail say the ticket never confirmed
    the guest — as opposed to confirming them."""
    sig = _sig("")
    matches_indicators(sig, IND, "Mariana", "Campos")
    assert sig["name_checked"] is False
    sig2 = _sig("Mariana Campos")
    matches_indicators(sig2, IND, "Mariana", "Campos")
    assert sig2["name_checked"] is True


def test_the_name_is_skipped_entirely_when_the_review_has_no_author():
    """No first and no last: there is nothing to compare from either side, and
    the ticket must not be marked as having been checked."""
    sig = _sig("Mariana Campos")
    ok, used = matches_indicators(sig, IND, None, None)
    assert ok is True
    assert "name" not in used
    assert sig["name_checked"] is False


# ── and the ticket it was written for, end to end ──────────────────────────

class _Ticket:
    """The Mariana shape: booking id in the BODY, guest-name field EMPTY."""
    id = "34136523"
    subject = "Wrong ticket purchased"
    description = ("I bought 4 tickets for Universal. Booking ID: 33204378. "
                   "The ticket was the wrong one.")
    created_at = "2026-08-01T10:00:00Z"
    custom_fields: list = []


@pytest.fixture()
def sparse_zendesk(monkeypatch):
    monkeypatch.setattr(Z, "is_live", lambda k: True)
    monkeypatch.setattr(Z, "_get_client", lambda: object())
    monkeypatch.setattr(Z, "_search_with_retry", lambda _z, q: [_Ticket()])
    monkeypatch.setattr(Z, "ticket_signals", lambda t: {
        "booking_id": "", "guest_name": "", "experience": "Universal Studios Orlando",
        "city": "Orlando", "visit_date": "2026-07-26", "guest_email": "",
        "pax_raw": "", "pax": None, "vendor_name": "", "itinerary_id": ""})


def _run(notes=None):
    return asyncio.run(shortlist(
        {"experience_or_venue": "Universal", "city_or_country": "Orlando",
         "issue_terms": ["wrong ticket"], "dates_mentioned": [],
         "visit_date_hint": "2026-07-26"},
        "Mariana", "Campos", notes=notes, review_date="2026-08-05"))


def test_the_sparse_ticket_now_produces_a_candidate(sparse_zendesk):
    """Both halves together: the name no longer rejects it, and the booking id
    is read out of the body because the field is empty."""
    out = _run()
    assert out, "the ticket was still discarded"
    assert out[0]["booking_id"] == "33204378", out[0]
    assert out[0]["bid_source"].startswith("text:"), out[0]


def test_the_card_is_told_the_name_was_never_verified(sparse_zendesk):
    """Surviving without a name comparison is new, and the reader is choosing
    a booking on this. Whatever "name" reaches matched_on came from the QUERY
    that found the ticket, not from the ticket agreeing about the guest."""
    notes = []
    _run(notes)
    assert any(n["kind"] == "name_unverified" for n in notes), notes


def test_a_sparse_ticket_for_the_WRONG_venue_is_still_discarded(monkeypatch,
                                                                sparse_zendesk):
    """The rule must not become "anything with a number in it"."""
    monkeypatch.setattr(Z, "ticket_signals", lambda t: {
        "booking_id": "", "guest_name": "", "experience": "Colosseum Guided Tour",
        "city": "Rome", "visit_date": "2020-01-01", "guest_email": "",
        "pax_raw": "", "pax": None, "vendor_name": "", "itinerary_id": ""})
    assert _run() == []
