"""A deliberate absence is not an unfinished job, and must not read like one.

The card said:

    no support-tag mapping for Operations Issue / Customer Support Issues -
    support contacts not compared

which reads as something somebody forgot to do. It is not. There is no
contact-reason tag for "unhappy with how you handled it", because that is not
why anyone opens a ticket, and mapping it to an adjacent tag would invent
history. Eleven of the pairs are like this.

This is the inverse of the failure CLAUDE.md opens with — not a broken
mechanism reported as an empty result, but a correct result reported as a
broken mechanism. Same cost: the reader cannot tell which they are looking at.
"""
import pytest

from server.services.insights import _no_mapping_note
from server.taxonomy import (L2_OPTIONS, SUPPORT_TAG_MAP,
                             SUPPORT_TAGS_NOT_APPLICABLE,
                             support_tags_for, support_tags_not_applicable)


def test_the_pair_from_the_card_explains_itself():
    """And says what to DO about it.

    The first version of this sentence claimed guests do not contact us about
    being refused a refund. They plainly do, in quantity — those contacts are
    filed under the reason the guest first wrote in about. Stating a falsehood
    confidently on the card is worse than the bare "no mapping" it replaced,
    so the note now names the next step instead of theorising about guests.
    """
    got = _no_mapping_note("Operations Issue", "Customer Support Issues",
                           "support contacts not compared")
    assert "no support-tag mapping" not in got, got
    assert "re-check L1/L2" in got, \
        "the note explains but does not say what would fix it"
    assert "not a contact reason" not in got, \
        "the claim that guests never write in about this is false"


def test_no_note_claims_guests_do_not_contact_support():
    """A blanket claim about guest behaviour is not something this file can
    know, and it was wrong the one time it was made."""
    for pair, why in SUPPORT_TAGS_NOT_APPLICABLE.items():
        low = why.lower()
        assert "not a contact reason" not in low, (pair, why)
        assert "not something guests" not in low, (pair, why)


def test_a_pair_that_is_genuinely_unmapped_still_says_so():
    """The two must stay tellable apart — one is somebody's job to fill in, the
    other never will be."""
    got = _no_mapping_note("Ticket Issues", "Some Unmapped L2",
                           "support contacts not compared")
    assert "no support-tag mapping" in got


def test_an_unclassified_review_is_a_third_thing():
    got = _no_mapping_note("", "", "support contacts not compared")
    assert "no L1 or L2 classification" in got
    assert "no support-tag mapping" not in got


def test_the_three_notes_read_differently():
    texts = {_no_mapping_note("Operations Issue", "Customer Support Issues", "t"),
             _no_mapping_note("Ticket Issues", "Some Unmapped L2", "t"),
             _no_mapping_note("", "", "t")}
    assert len(texts) == 3


# ── the list has to join to the real taxonomy ───────────────────────────────

@pytest.mark.parametrize("pair", sorted(SUPPORT_TAGS_NOT_APPLICABLE))
def test_every_excused_pair_is_a_real_pair(pair):
    """A pair spelled wrong here is never consulted, and the card falls back to
    the sentence this whole file exists to stop — silently. The same join bug
    as `"ZD-4491"` against ticket_id `"4491"`."""
    l1, l2 = pair
    assert l1 in L2_OPTIONS, f"{l1!r} is not an L1"
    assert l2 in L2_OPTIONS[l1], f"{l2!r} is not an L2 of {l1!r}"


@pytest.mark.parametrize("pair", sorted(SUPPORT_TAGS_NOT_APPLICABLE))
def test_an_excused_pair_is_not_also_mapped(pair):
    """Excusing a pair that HAS tags would suppress a comparison that can
    actually be made."""
    assert pair not in SUPPORT_TAG_MAP, \
        f"{pair} has support tags and is also excused from having them"
    assert support_tags_for(*pair) is None


@pytest.mark.parametrize("pair", sorted(SUPPORT_TAGS_NOT_APPLICABLE))
def test_every_excuse_is_a_reason_not_a_restatement(pair):
    why = SUPPORT_TAGS_NOT_APPLICABLE[pair]
    assert why and len(why.split()) >= 5, f"{pair}: {why!r} explains nothing"
    assert "no support-tag mapping" not in why, \
        f"{pair}: the excuse just repeats the sentence it replaces"


def test_a_pair_with_no_entry_returns_the_empty_string_not_a_reason():
    """"" means the absence is unexplained, which is a different fact from an
    explained one and must not be dressed up as one."""
    assert support_tags_not_applicable("Ticket Issues", "Wrong Tickets") == ""
    assert support_tags_not_applicable("", "") == ""


def test_every_pair_is_either_mapped_or_explained():
    """No pair is left to produce the bare "no support-tag mapping" sentence.

    That sentence is now reachable only for an L2 outside the taxonomy — so a
    new L2 added without tags AND without a reason fails here rather than
    surfacing as an unexplained blank on somebody's card.
    """
    gaps = [f"{l1} / {l2}"
            for l1, l2s in L2_OPTIONS.items() for l2 in l2s
            if support_tags_for(l1, l2) is None
            and not support_tags_not_applicable(l1, l2)]
    assert gaps == [], (
        "these L1/L2 pairs have no support tags and no stated reason, so the "
        "card will say a gap was forgotten without knowing whether it was:\n"
        + "\n".join(gaps))
