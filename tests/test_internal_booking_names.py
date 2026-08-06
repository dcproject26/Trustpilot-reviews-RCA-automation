"""A booking labelled by our own desk is not a guest to compare against.

"Customer Ops Lead" is what our systems write on a corporate or desk-made
booking. No guest types it. Compared to a reviewer's name it produced a
disagreement that means nothing — on the signal this check leans on hardest.

THE ASYMMETRY IS CORRECT AND IS NOT THE BUG. A name that AGREES is strong
evidence: the guest name is the second strongest identifier after the booking
id, and it is the only thing that separates two bookings at the same venue on
the same date, which is exactly where venue and date have nothing left to say.
A name that DISAGREES is weak, because a booking can legitimately sit under a
partner's, a colleague's or a company's name. Agreement stays a match; that
was proposed as a "fix" and would have thrown away the second-best identifier
we have.

What was missing is the mirror of `is_placeholder`. That covers the REVIEW
side — a review posted as "customer" carries no identifier. The BOOKING side
had no equivalent, so our own label was treated as a person.
"""
import pytest

from server.names import is_internal_booking_name
from server.bid_indicator_check import check


@pytest.mark.parametrize("name", [
    "Customer Ops Lead", "customer ops lead", "CUSTOMER OPS LEAD",
    "Customer Ops", "Ops Lead", "Headout", "internal booking",
    "corporate booking", "test booking", "not provided", "unknown guest",
])
def test_an_internal_label_is_recognised(name):
    assert is_internal_booking_name(name) is True, name


@pytest.mark.parametrize("name", [
    "Anna Ops",            # "Ops" is a real surname; only the whole phrase counts
    "Elizabeth Gist", "Mariana Campos", "Ioan Popescu",
    "Lead Fernandes", "Customer Cariello",
])
def test_a_real_name_is_never_taken_for_a_label(name):
    """A false positive here silences the strongest signal after the BID —
    strictly worse than the bug being fixed."""
    assert is_internal_booking_name(name) is False, name


def test_an_empty_name_is_not_an_internal_label():
    """Different fact, already handled by a different branch with a different
    sentence. Merging them would lose which one happened."""
    assert is_internal_booking_name("") is False
    assert is_internal_booking_name(None) is False


# ── and what the check does with it ────────────────────────────────────────

BOOKING = {"experienceName": "Swiss Travel Pass", "primary_guest_name": ""}


def _check(pgn, author="Ioan Popescu"):
    return check("bad experience", {**BOOKING, "primary_guest_name": pgn},
                 author=author, received_at="2026-08-05")


def test_an_internal_label_reports_unchecked_not_a_disagreement():
    got = _check("Customer Ops Lead")
    assert got["state"] == "unchecked", got["state"]
    assert "guest" not in (got.get("contradictions") or []), got


def test_the_reason_names_the_label_rather_than_blaming_the_guest():
    """"The booking has no readable guest name" and "the booking is recorded
    under an internal label" are different facts, and the second one says the
    RECORD needs fixing."""
    got = _check("Customer Ops Lead")
    assert "internal label" in got["why"], got["why"]
    assert "Customer Ops Lead" in got["why"], got["why"]


def test_a_name_that_agrees_is_still_a_match():
    """The asymmetry this file exists to protect. Downgrading agreement to
    "unchecked" would discard the second-best identifier after the BID."""
    got = _check("Ioan Popescu")
    assert got["state"] == "match", got
    assert "agrees" in got["why"], got["why"]


def test_a_real_disagreement_is_still_not_decisive_on_its_own():
    """The other half of the asymmetry, unchanged: a booking under someone
    else's name is common enough that it must not raise the flag alone."""
    got = _check("Fredrik Andersson")
    assert got["state"] != "mismatch", got
