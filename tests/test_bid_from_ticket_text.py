"""The booking id is not always in the booking-id field.

A support ticket was found by searching the guest's name and venue. It carried
the booking id in its body. The shortlist dropped it, silently, at:

    sig = ticket_signals(t)
    bid = sig.get("booking_id")      # the CUSTOM FIELD, and nothing else
    if not bid:
        continue                     # ← the ticket's life ends here

and the review was filed as untraceable. Two faults at once:

  * TWO CONTRADICTORY RULES IN ONE FILE. `find_bids_by_requester_name` in this
    same module has always harvested subject + custom fields + body, on the
    stated grounds that the field "is frequently left empty". `shortlist` read
    the field alone. So one path found the ticket and the other threw it away.
  * A FOUND-AND-DISCARDED TICKET LOOKED EXACTLY LIKE A TICKET NEVER FOUND —
    CLAUDE.md §1, and the reason it took three rounds of diagnosis.

The fallback cannot be a free pass. A number in the custom field is Zendesk
asserting "this is a booking id"; a number in a sentence is us deciding it
looks like one, and shortlist runs no BigQuery to check. So a text-derived id
is admitted ONLY when the ticket's other indicators agree — name AND venue AND
date — which is exactly the ticket the search was built to find, with the
field left blank by whoever raised it.
"""
import pytest

from server.services.zendesk import bids_from_ticket_text


class T:
    """The two attributes the extractor reads. Zenpy tickets are objects, not
    dicts, which is why this is a class and not a literal."""
    def __init__(self, subject="", description=""):
        self.subject, self.description = subject, description


# ── it finds what the field did not have ───────────────────────────────────

def test_a_labelled_id_in_the_body_is_found():
    """The reported case. 'Booking ID: 33118844' in the first line, nothing in
    the custom field, ticket discarded."""
    bids, prov = bids_from_ticket_text(
        T(description="Hi, my Booking ID: 33118844 was never delivered."))
    assert bids == ["33118844"], bids
    assert prov == "labelled"


def test_the_subject_is_read_as_well_as_the_body():
    bids, _ = bids_from_ticket_text(T(subject="Refund for booking 40551237",
                                      description="see above"))
    assert bids == ["40551237"], bids


@pytest.mark.parametrize("phrase", [
    "Booking ID: 33118844", "booking id 33118844", "BID 33118844",
    "booking no. 33118844", "Order #33118844", "reference: 33118844",
    "Reservation number 33118844", "confirmation 33118844",
])
def test_the_wordings_guests_and_agents_actually_use(phrase):
    bids, prov = bids_from_ticket_text(T(description=f"about {phrase} please"))
    assert bids == ["33118844"], (phrase, bids)
    assert prov == "labelled"


def test_a_bare_number_is_found_but_marked_as_bare():
    """Worth less than a labelled one and must not be indistinguishable from
    it — the caller weighs them differently."""
    bids, prov = bids_from_ticket_text(T(description="ticket about 33118844"))
    assert bids == ["33118844"]
    assert prov == "bare"


def test_a_labelled_id_wins_over_bare_numbers_in_the_same_ticket():
    """A ticket saying "Booking ID: 33118844 ... I paid on 20250114" must not
    offer the date-like number as an equal candidate."""
    bids, prov = bids_from_ticket_text(
        T(description="Booking ID: 33118844. Paid 20250114 by card 40551237."))
    assert bids == ["33118844"], bids
    assert prov == "labelled"


# ── and does not invent one ────────────────────────────────────────────────

def test_a_ticket_with_no_numbers_yields_nothing_and_says_so():
    """The empty provenance is what lets the caller tell "no id here" from
    "an id we are unsure about"."""
    bids, prov = bids_from_ticket_text(T(description="the guide never arrived"))
    assert bids == []
    assert prov == ""


def test_ticket_ids_are_excluded():
    """Zendesk ticket ids and Headout booking ids share the same numeric
    space. "duplicate of 33979875" must not become a candidate booking."""
    bids, _ = bids_from_ticket_text(
        T(description="duplicate of 33979875"), exclude={"33979875"})
    assert bids == []


def test_a_short_number_is_not_a_booking_id():
    """Below seven digits is a pax count, a price, a house number."""
    bids, _ = bids_from_ticket_text(T(description="we were 4 people, seat 1123"))
    assert bids == []


def test_a_long_number_is_not_a_booking_id():
    """Above twelve is a card number or a phone with a country code."""
    bids, _ = bids_from_ticket_text(T(description="call +4915112345678901"))
    assert bids == []


def test_a_wall_of_numbers_is_capped_rather_than_becoming_a_page_of_candidates():
    """One ticket must not turn into a candidate list nobody can read. The cap
    is a JUDGEMENT and the caller announces it on the trail."""
    body = " ".join(str(30000000 + i) for i in range(12))
    bids, _ = bids_from_ticket_text(T(description=body))
    assert len(bids) <= 3, bids


def test_duplicates_collapse():
    """The same id in the subject and the body is one candidate, not two."""
    bids, _ = bids_from_ticket_text(
        T(subject="booking 33118844", description="booking 33118844 again"))
    assert bids == ["33118844"]


def test_the_body_is_bounded_so_a_huge_thread_cannot_stall_the_search():
    """Long ticket bodies are whole email threads. Reading all of one per
    ticket, times fifteen tickets, times several queries, is the difference
    between a search and a stall."""
    bids, _ = bids_from_ticket_text(
        T(description=("x" * 8000) + " booking 33118844"))
    assert bids == [], "the body should be truncated before scanning"


# ── the rules the caller enforces, pinned where they are stated ────────────
#
# These are NEGATIVE source assertions — permitted by CLAUDE.md because
# unreachability cannot defeat "this string appears nowhere". The positive
# behaviour needs a live Zendesk client, which no test here has.

def test_the_shortlist_no_longer_drops_a_ticket_the_moment_the_field_is_empty():
    """The exact three lines that caused this. If they come back, so does the
    silent discard."""
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk.shortlist)
    assert 'bid = sig.get("booking_id")\n                if not bid:\n                    continue' not in src, (
        "shortlist drops tickets whose booking-id FIELD is empty again — the "
        "body fallback has been removed")


def test_a_text_derived_id_is_not_admitted_on_a_name_alone():
    """The guard that keeps a scraped number honest: full indicator agreement,
    or it does not become a candidate."""
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk.shortlist)
    assert 'if sig.get("bid_from_text") and not ok:' in src, (
        "the indicator-agreement guard on text-derived booking ids is gone")


def test_a_found_but_unusable_ticket_is_reported_rather_than_dropped():
    """CLAUDE.md §1: "I ran and found nothing" must not look like "I did not
    run". A ticket found and discarded has to leave a trace."""
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk.shortlist)
    for kind in ('"no_bid"', '"text_bid_unconfirmed"', '"ambiguous_bid"'):
        assert kind in src, f"shortlist no longer reports {kind}"
