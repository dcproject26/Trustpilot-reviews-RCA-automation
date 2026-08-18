"""A booking id written as `ID33508897` is a booking id.

BID_REGEX was `\\b\\d{7,12}\\b`. `\\b` needs a word/non-word transition and a
LETTER is a word character, so there is no boundary between the `D` and the `3`
— the pattern could not see the digits in `ID33508897`.

Laura Ramírez's review carried "Reference number: Reserva ID33508897". The card
printed "no BID in text" directly beneath the text containing it, the review
was searched in Zendesk on the guest's NAME instead, and it sat at T2 asking an
associate to confirm a booking the guest had already given us.

`(?<!\\d)\\d{7,12}(?!\\d)` states the actual intent: 7-12 digits not part of a
longer number. Nothing else about the rule moves — the length bounds are the
same, and that is what the second half of this file pins.
"""
import re

import pytest

from server.taxonomy import BID_REGEX


def _find(text):
    m = re.search(BID_REGEX, text)
    return m.group(0) if m else None


# ── the defect ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "ID33508897",
    "Reserva ID33508897",
    "Reference number: Reserva ID33508897",
    "BID33508897",
    "bookingID33508897",
])
def test_a_bid_touching_a_letter_is_found(text):
    """THE POINT. Every one of these returned None."""
    assert _find(text) == "33508897", (
        f"the booking id in {text!r} is invisible to BID_REGEX")


def test_lauras_review_yields_its_booking_id():
    """The exact review body from the card that reported no BID in text."""
    body = ("Mucho cuidado!!!!!! Después de pagar me han cancelado cuatro "
            "horas después porque según ellos no hay disponibilidad\n"
            "Reference number: Reserva ID33508897")
    assert _find(body) == "33508897"


# ── what must NOT have moved ────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "123456",             # 6 digits — too short
    "1234567890123",      # 13 digits — too long
    "123456789012345",    # 15 digits
    "2026-08-15",         # a date: no run is long enough
    "",
])
def test_the_length_bounds_are_unchanged(text):
    """Widening the boundary must not widen the RANGE. A 13-digit number was
    not a booking id before and is not one now."""
    assert _find(text) is None, f"{text!r} should not read as a booking id"


@pytest.mark.parametrize("text,expected", [
    ("booking 33508897", "33508897"),
    ("#33508897", "33508897"),
    ("ref:33508897", "33508897"),
    ("33508897.", "33508897"),
    ("33508897", "33508897"),
])
def test_the_cases_that_already_worked_still_do(text, expected):
    assert _find(text) == expected


def test_a_long_number_is_not_salami_sliced():
    """A 15-digit string must not yield its first 12 digits — that would be a
    fabricated booking id, which is worse than none."""
    assert _find("123456789012345") is None


# ── through the ingest, not just the pattern ────────────────────────────────

def test_the_parser_puts_it_on_the_review():
    """Driven through parse_review, because the regex being right and the
    reference_number field being populated are two different claims."""
    from server.services.slack import parse_review

    ev = {"ts": "1.0", "channel": "C_ORM",
          "text": "★☆☆☆☆ Laura Ramírez\nMucho cuidado\n"
                  "Reference number: Reserva ID33508897"}
    assert parse_review(ev)["reference_number"] == "33508897"
