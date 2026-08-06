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
    assert "check manually" in got["detail"], got


def test_the_card_sentence_says_we_looked_not_that_inputs_were_missing():
    """The reader gets an answer they can act on, not a plumbing report.

    "no booking total; no pax count" reads as an outage, and a reviewer who
    thinks the check is broken stops reading it. The card names the next step;
    the reasons go to the trail.
    """
    got = check_overcharge({"pax": 1}, ["CHF 100.00 per person"])
    assert got["detail"] == ("Could not verify this from the Zendesk case — "
                             "check manually."), got
    assert "no booking total" not in got["detail"], got


def test_the_reasons_survive_on_the_trail_field():
    """"We could not tell" is only useful if SOMETHING says what would have
    told us. It moved off the card, it did not disappear."""
    got = check_overcharge({"pax": 1}, ["CHF 100.00 per person"])
    assert any("no booking total" in r for r in got["unsettled_because"]), got
    got2 = check_overcharge({"amount": "100"}, ["CHF 100.00 per person"])
    assert any("no pax" in r for r in got2["unsettled_because"]), got2


def test_a_settled_answer_carries_the_same_keys_as_an_unsettled_one():
    """A caller must never have to guess whether the field is there."""
    settled = check_overcharge({"amount": "200", "pax": 2},
                               ["CHF 100.00 per person"])
    assert settled["unsettled_because"] == [], settled


# ── the key bug: the warehouse writes amountUSD ────────────────────────────

def test_the_total_is_read_from_the_key_the_warehouse_actually_writes():
    """`_get_booking_amount` writes `amountUSD` (price_payable_usd). The
    original key list held six plausible spellings and not that one, so every
    live booking reported "no booking total" — a lookup that never ran,
    indistinguishable from a booking with no amount. Same shape as
    `show_draft --bid` reading `bookingId` off a row keyed `id`."""
    got = check_overcharge({"amountUSD": 461.19},
                           ["CHF 230.60 per person; Pax: 1"])
    assert got["verdict"] == "charged_for_more", got
    assert not any("no booking total" in r for r in got["unsettled_because"]), got


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


# ── pax, because the warehouse has no column for it ────────────────────────

def test_pax_comes_off_a_labelled_field_in_the_case():
    """No query in this repo selects a pax column. Without reading it from the
    ticket dump the arithmetic could never run on a real booking, and the check
    would answer "check manually" forever — barely better than dead code."""
    from server.price_check import pax_from_text
    for text, want in [("Pax: 2", 2), ("No. of guests: 3", 3),
                       ("Quantity: 4", 4), ("2 Adults", 2), ("1 x Adult", 1)]:
        got, why = pax_from_text([text])
        assert got == want, (text, got)
        assert "Zendesk" in why, why


def test_an_unlabelled_number_is_not_read_as_pax():
    """A bare figure in prose is as likely to be the disputed quantity as the
    recorded one, and reading the wrong one inverts the answer."""
    got, why = pax_from_text_or_zero("The guest says they were charged for 2")
    assert got == 0, (got, why)


def pax_from_text_or_zero(text):
    from server.price_check import pax_from_text
    return pax_from_text([text])


def test_where_pax_came_from_is_stated_because_it_is_a_judgement():
    """Reading pax off a ticket instead of the record is a decision, and the
    reader is told one was made."""
    from server.price_check import pax_from_text
    _, why = pax_from_text([])
    assert "no pax column" in why, why


# ── the gate: this is what actually reaches a card ─────────────────────────

def test_an_amount_claim_the_case_cannot_settle_is_demoted_to_unknown():
    """The verdict this exists to stop: "Inaccurate — the record says one
    adult", which contradicts a guest on the strength of the record repeating
    itself."""
    from server.price_check import gate_amount_claim
    got = gate_amount_claim("I booked one ticket but was charged for 2",
                            "Inaccurate", {"amountUSD": 461.19},
                            ["Booking confirmed; CHF 461.19 total"])
    assert got is not None
    acc, note = got
    assert acc == "Unknown", got
    assert "check manually" in note, note


def test_a_claim_that_is_not_about_money_is_left_alone():
    """The gate can only demote, so a false positive silently turns a settled
    answer into "check manually" — the expensive direction."""
    from server.price_check import gate_amount_claim
    for claim in ["The guide was rude",
                  "We were refunded two weeks later",
                  "There were two of us and the queue was long"]:
        assert gate_amount_claim(claim, "Accurate", {}, []) is None, claim


def test_an_already_unknown_verdict_is_not_re_demoted():
    from server.price_check import gate_amount_claim
    assert gate_amount_claim("charged twice", "Unknown", {}, []) is None


def test_a_case_that_does_settle_it_leaves_the_verdict_standing():
    """Not running and running-and-finding-it-fine must not look the same:
    this returns None, and the demotion path returns a note."""
    from server.price_check import gate_amount_claim
    got = gate_amount_claim("I was charged twice", "Accurate",
                            {"amountUSD": 461.19},
                            ["CHF 230.60 per person; Pax: 1"])
    assert got is None, got


def test_the_case_disagreeing_with_the_model_is_reported_not_silently_kept():
    """Two answers to one question. The verdict stands — the arithmetic is not
    authority enough to overturn it — but the reader is told they disagreed."""
    from server.price_check import gate_amount_claim
    got = gate_amount_claim("I was charged twice", "Inaccurate",
                            {"amountUSD": 461.19},
                            ["CHF 230.60 per person; Pax: 1"])
    assert got is not None, "a disagreement that reports nothing is a silent keep"
    acc, note = got
    assert acc == "Inaccurate", "the verdict is left as written"
    assert "disagree" in note, note


# ── the feeder, driven ─────────────────────────────────────────────────────
# The pure functions above are thoroughly tested and that is exactly how the
# last two mutation survivors got in: every survivor was a feeder left
# undriven while the function it fed was covered. These drive validate().

def _wwr(claim, accuracy):
    return {"what_went_wrong": {"guest_issues": [
        {"issue": "Charged twice", "claim": claim,
         "claim_accuracy": accuracy, "case_side": "guest disputed the amount"}]}}


def test_validate_actually_runs_the_gate():
    """A gate wired into no path looks exactly like one that works."""
    from server.services.rca_v4_validate import validate
    out, notes = validate(_wwr("I booked one ticket but was charged for 2",
                               "Inaccurate"),
                          booking={"amountUSD": 461.19},
                          events=[{"raw_body": "Booking confirmed; CHF 461.19 total"}])
    assert out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] == "Unknown"
    assert any("check manually" in n for n in notes), notes


def test_the_gate_reads_the_key_the_events_actually_carry():
    """Zendesk writes the comment body to `raw_body`. Reading any other key
    finds nothing on every real run and looks identical to a case that states
    no per-person amount."""
    from server.services.rca_v4_validate import validate
    out, _ = validate(_wwr("I was charged twice", "Inaccurate"),
                      booking={"amountUSD": 461.19},
                      events=[{"raw_body": "CHF 230.60 per person; Pax: 1"}])
    # The case settles it, so the verdict is NOT demoted to Unknown.
    assert out["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] != "Unknown"


def test_the_gate_falls_back_to_summary_when_that_is_all_a_build_kept():
    from server.services.rca_v4_validate import validate
    out, notes = validate(_wwr("I was charged twice", "Inaccurate"),
                          booking={"amountUSD": 461.19},
                          events=[{"summary": "CHF 230.60 per person; Pax: 1"}])
    assert any("disagree" in n for n in notes), notes


def test_a_demotion_reaches_the_trail_because_a_silent_rewrite_is_the_bug():
    from server.services.rca_v4_validate import validate
    _, notes = validate(_wwr("I was charged for 2", "Accurate"),
                        booking={}, events=[])
    assert any("Unknown" in n and "check manually" in n for n in notes), notes


def test_validate_without_a_booking_does_not_raise():
    """The gate must never be the thing that loses an RCA."""
    from server.services.rca_v4_validate import validate
    for bk in (None, {}, {"amountUSD": None}):
        out, _ = validate(_wwr("charged twice", "Inaccurate"), booking=bk,
                          events=None)
        assert out["what_went_wrong"]["guest_issues"], bk
