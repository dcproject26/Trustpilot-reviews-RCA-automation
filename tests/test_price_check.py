"""Was the guest charged for one ticket or two?

THE CIRCULAR ANSWER THIS EXISTS TO REPLACE. A review said "booked one ticket,
charged for 2". The RCA answered Inaccurate, citing "Booking 32142070 records
one adult, CHF 461.19 total, no add-ons" — the booking record confirming its
own pax count. That proves nothing. The question is not how many tickets the
record says; it is whether CHF 461.19 is the price of ONE of them or TWO.

Settling it needs a UNIT price, which the total and the pax count cannot
supply between them. "We could not establish it" is a legitimate answer and
must not be dressed up as either verdict: a guest wrongly told they were not
double-charged is worse than one told we could not tell.
"""
import pytest

from server.price_check import (check_overcharge, unit_price_from_text,
                                amounts_in)


def test_the_reported_case_is_answered_correctly():
    """CHF 461.19 for one adult, where one costs CHF 230.60."""
    got = check_overcharge({"amount": "461.19", "pax": 1},
                           ["Booking confirmed; CHF 230.60 per person; 2 Adults"])
    assert got["verdict"] == "charged_for_more", got
    assert "2.0x" in got["detail"], got


def test_a_total_matching_the_pax_count_says_so():
    got = check_overcharge({"amount": "461.19", "pax": 1},
                           ["CHF 461.19 per person"])
    assert got["verdict"] == "matches", got


def test_two_guests_at_the_unit_price_is_not_an_overcharge():
    got = check_overcharge({"amount": "461.20", "pax": 2},
                           ["CHF 230.60 per person"])
    assert got["verdict"] == "matches", got


# ── what it refuses to answer ──────────────────────────────────────────────

def test_no_unit_price_anywhere_is_UNESTABLISHED_not_a_verdict():
    """The whole point. Falling back on the booking record here is what
    produced the circular answer."""
    got = check_overcharge({"amount": "461.19", "pax": 1},
                           ["Booking confirmed; CHF 461.19 total"])
    assert got["verdict"] == "unestablished", got
    assert "cannot settle this on its own" in got["detail"], got


def test_the_unestablished_answer_names_what_was_missing():
    """"We could not tell" is only useful if it says what would have told us."""
    got = check_overcharge({"pax": 1}, ["CHF 100.00 per person"])
    assert "no booking total" in got["detail"], got
    got2 = check_overcharge({"amount": "100", "pax": 0}, ["CHF 100.00 per person"])
    assert "no pax count" in got2["detail"], got2


def test_no_tickets_at_all_does_not_raise():
    for v in (None, [], [""]):
        assert check_overcharge({"amount": "1", "pax": 1}, v)["verdict"] == "unestablished"


def test_a_total_that_divides_untidily_asks_for_a_human():
    """Neither the pax count nor a clean multiple of it. Guessing either way
    here is how a real overcharge gets closed as accurate."""
    got = check_overcharge({"amount": "340.00", "pax": 1},
                           ["CHF 230.60 per person"])
    assert got["verdict"] == "unestablished", got
    assert "needs a human" in got["detail"], got


# ── reading the amounts ────────────────────────────────────────────────────

@pytest.mark.parametrize("text,cur,val", [
    ("CHF 461.19", "CHF", 461.19), ("PLN 606.00", "PLN", 606.0),
    ("EUR 152.65", "EUR", 152.65), ("€140.01", "EUR", 140.01),
    ("£12.50", "GBP", 12.5), ("USD 61.76", "USD", 61.76),
    ("CHF 1,234.56", "CHF", 1234.56),
])
def test_the_money_shapes_that_appear_on_real_tickets(text, cur, val):
    got = amounts_in(f"total {text} charged")
    assert got and got[0][0] == cur and got[0][1] == val, got


def test_a_net_amount_is_never_read_as_what_the_guest_paid():
    """"PLN 606.00 net PLN 450.00" — the second is what we paid the partner.
    Reading one for the other tells a guest they were charged 450 when they
    paid 606."""
    price, _, why = unit_price_from_text(["PLN 606.00 net PLN 450.00 per person"])
    assert price is None, (price, why)


def test_only_an_explicitly_per_person_amount_is_used_as_the_unit():
    """An unlabelled figure on a ticket is the TOTAL far more often than the
    unit price, and guessing wrong flips the answer."""
    assert unit_price_from_text(["Booking total CHF 461.19"])[0] is None
    assert unit_price_from_text(["CHF 230.60 per adult"])[0] == 230.60


@pytest.mark.parametrize("phrase", ["per person", "per adult", "per pax",
                                    "per ticket", "each", "pp"])
def test_the_wordings_that_mark_a_unit_price(phrase):
    assert unit_price_from_text([f"CHF 230.60 {phrase}"])[0] == 230.60


def test_rounding_does_not_read_as_a_second_ticket():
    """Fees and rounding move a total by small amounts; a second ticket
    doubles it. Nothing real sits between those."""
    got = check_overcharge({"amount": "461.50", "pax": 2},
                           ["CHF 230.60 per person"])
    assert got["verdict"] == "matches", got


def test_the_answer_says_which_source_settled_it():
    """An amount read off a ticket and one nobody could find must not read the
    same."""
    settled = check_overcharge({"amount": "461.19", "pax": 1},
                               ["CHF 230.60 per person"])
    unsettled = check_overcharge({"amount": "461.19", "pax": 1}, [])
    assert settled["source"] == "zendesk", settled
    assert unsettled["source"] == "", unsettled
