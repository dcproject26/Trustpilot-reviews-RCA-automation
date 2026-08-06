"""The supply partner's name and contact details never reach the card.

A real card read:

    Booking confirmed email sent to guest; partner ref K507100323;
    RAIL EUROPE-CHF contact +41 33 828 72 33

— a vendor's trading name and a phone number, on a card about a guest's
complaint.

THE PROMPT ALREADY SAID NOT TO. That was the wrong mechanism twice over: an
instruction can be ignored, and it does nothing at all for the drafts already
stored. This is enforced in code at render, so it holds whatever the model
wrote and whether the draft is new or a year old.

THE REFERENCE STAYS. "partner ref K507100323" is what someone uses to find the
booking on the partner's side. The NAME and the CONTACT DETAILS are what crowd
out the fact the row exists to carry.
"""
import pytest

from server.ticket_notes import scrub_vendor

V = "RAIL EUROPE- CHF"


def test_the_vendor_name_becomes_the_supply_partner():
    got = scrub_vendor("vendor RAIL EUROPE-CHF; net CHF 415.98", V)
    assert "RAIL" not in got.upper(), got
    assert "the supply partner" in got, got


def test_spacing_and_punctuation_drift_is_tolerated():
    """The warehouse writes "RAIL EUROPE- CHF" and the ticket text writes
    "RAIL EUROPE-CHF". An exact match would catch neither reliably."""
    for written in ("RAIL EUROPE-CHF", "RAIL EUROPE - CHF", "Rail Europe CHF"):
        got = scrub_vendor(f"sent to {written} today", V)
        assert "the supply partner" in got, (written, got)


def test_a_phone_number_is_removed():
    got = scrub_vendor("contact +41 33 828 72 33 for details", V)
    assert "828" not in got, got
    assert "+41" not in got, got


def test_an_email_address_is_removed():
    got = scrub_vendor("Guest emailed us at guest@example.com about it", V)
    assert "@" not in got, got


def test_a_dangling_preposition_goes_with_the_detail():
    """"emailed us at " reads worse than the detail it replaced."""
    got = scrub_vendor("Guest emailed us at guest@example.com about it", V)
    assert "us about it" in got, got


def test_the_label_and_the_replacement_do_not_double_up():
    """"vendor the supply partner" reads as a bug."""
    got = scrub_vendor("vendor RAIL EUROPE-CHF confirmed", V)
    assert "vendor the supply partner" not in got.lower(), got


def test_the_partner_reference_survives():
    """It is what finds the booking on the partner's side — the one identifier
    here that is any use."""
    got = scrub_vendor("partner ref K507100323; RAIL EUROPE-CHF", V)
    assert "K507100323" in got, got


@pytest.mark.parametrize("keep", ["CHF 415.98", "2 Adults", "03 Aug 08:30",
                                  "HEA-97947961", "ref K507100323"])
def test_the_facts_the_row_exists_for_survive(keep):
    got = scrub_vendor(f"details sent to RAIL EUROPE-CHF; {keep}", V)
    assert keep in got, got


def test_a_person_named_as_the_vendor_is_scrubbed_too():
    """The partner is sometimes an individual — "EMILIAN STACHURA" — and a
    person's name is more sensitive than a company's, not less."""
    got = scrub_vendor("Booking intimation sent to EMILIAN STACHURA; 2 Adults",
                       "EMILIAN STACHURA")
    assert "EMILIAN" not in got.upper(), got
    assert "2 Adults" in got, got


def test_a_common_word_in_the_vendor_name_does_not_eat_the_sentence():
    """"Rail", "Europe" and "Travel" appear in ordinary prose. Blanking every
    occurrence would leave a row nobody can read."""
    got = scrub_vendor("Guest asked about rail travel across Europe",
                       "RAIL EUROPE- CHF")
    assert "rail travel" in got.lower(), got


def test_an_empty_body_does_not_raise():
    for v in ("", None, "   "):
        assert scrub_vendor(v, V).strip() == ""


def test_no_vendor_name_still_removes_contact_details():
    """Whose number it is does not matter — none of them belong here, and a
    number left in is the one thing on this card that could be dialled by
    mistake."""
    got = scrub_vendor("call +41 33 828 72 33", "")
    assert "828" not in got, got


# ── and it is applied where the card reads from ────────────────────────────

def test_the_rendered_timeline_is_scrubbed():
    """At render, so a draft stored before this existed is fixed without a
    re-run."""
    from server.api import _scrub_timeline
    rows = _scrub_timeline(
        [{"summary": "sent; RAIL EUROPE-CHF contact +41 33 828 72 33",
          "label": "Tickets sent"}],
        {"vendorName": V})
    assert "RAIL" not in rows[0]["summary"].upper(), rows
    assert "828" not in rows[0]["summary"], rows


def test_scrubbing_survives_a_row_that_is_not_a_dict():
    """It runs on every render and must not die over one malformed row."""
    from server.api import _scrub_timeline
    assert _scrub_timeline([None, "x", {"summary": "ok"}], {})[-1]["summary"] == "ok"
