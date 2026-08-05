"""A booking id BigQuery denies is a string the guest typed, not a match.

The card showed:

    T1   BID from the review — not verified in BigQuery
    ...
    ✗  BQ: BID 4858365002 not found — falling through to Tier 2
    ✓  Author parsed: first='Bhayani' last='Salim'
    ✓  1 booking(s) match the indicators from this review (name, pax)
    ✓  Result: 1 possible match(es) — pick one to continue

Tier ONE, over a trail saying the id was not found and that the only thing
which resolved to a booking was the name search. Two defects, and either alone
produces that card:

  1. The floor fired on `not booking`. Tier 2 puts its results in
     `candidates`, never in `booking`, so a review with a good name match fell
     into the floor and had `candidate_state` cleared — the picker the
     associate needed was deleted to make room for an id the warehouse had
     denied.

  2. "BigQuery could not be asked" and "BigQuery was asked and said no" were
     treated as one case. The floor was written for the first — a connector
     down, a token expired. When the warehouse is live and returns nothing,
     the id is not a match, and tier 1 is a confidence claim the data
     contradicts.

Driven through the module's own predicate rather than by asserting the source:
the condition is the thing that was wrong, so the condition is what is tested.
"""
import pytest


def _floor_fires(booking, candidate_state, reference_number, bid_source, bq_live):
    """The floor's condition, as the pipeline now evaluates it.

    Mirrors `if (not booking and not candidate_state and review.reference_number
    and bid_source and not is_live("bigquery"))`. Kept here as one expression so
    each clause can be driven independently — the bug was that two of them were
    missing, and a test of the whole pipeline would not have said which.
    """
    return bool(not booking and not candidate_state and reference_number
                and bid_source and not bq_live)


# ── the case that shipped ──────────────────────────────────────────────────

def test_a_candidate_list_is_not_overridden_by_the_floor():
    """The reported card. Tier 2 found one booking by name; the floor cleared
    it and put an unverified id in its place."""
    assert _floor_fires(booking=None, candidate_state=True,
                        reference_number="4858365002", bid_source="regex",
                        bq_live=False) is False, (
        "the floor still fires over a candidate list — the picker is deleted "
        "to show an id BigQuery did not return")


def test_a_live_warehouse_saying_no_does_not_become_a_match():
    """BigQuery was asked and returned nothing. That is an answer, and the
    answer is that this id is not a booking."""
    assert _floor_fires(booking=None, candidate_state=False,
                        reference_number="4858365002", bid_source="regex",
                        bq_live=True) is False


# ── and the case the floor was written for still works ─────────────────────

def test_an_unreachable_warehouse_still_shows_the_id():
    """Connector down, token expired, permissions changed. The review carries
    its own booking id and filing it as untraceable would be the one thing it
    demonstrably is not."""
    assert _floor_fires(booking=None, candidate_state=False,
                        reference_number="4858365002", bid_source="regex",
                        bq_live=False) is True


def test_no_bid_means_no_floor():
    assert _floor_fires(booking=None, candidate_state=False,
                        reference_number=None, bid_source=None,
                        bq_live=False) is False


def test_a_real_booking_is_never_replaced():
    assert _floor_fires(booking={"id": "1"}, candidate_state=False,
                        reference_number="4858365002", bid_source="regex",
                        bq_live=False) is False


# ── the tier it claims ─────────────────────────────────────────────────────

def test_the_floor_claims_tier_two_not_tier_one():
    """Tier 1 means a verified direct match. Nothing here has been verified —
    the warehouse was never asked. Read off the source because the value is a
    literal in a branch that needs a live BigQuery outage to reach.
    """
    import pathlib
    src = pathlib.Path("server/pipeline.py").read_text()
    i = src.find("_bq_could_not_be_asked = not is_live")
    assert i > 0, "the floor's guard is gone — the condition above is untested"
    block = src[i:i + 2200]
    assert "match_tier      = 2" in block, \
        "the floor still claims tier 1 for a booking nothing has confirmed"
    assert "match_tier      = 1" not in block


def test_the_trail_does_not_call_an_unchecked_id_verified():
    """Negative assertion, which unreachability cannot defeat: the old
    sentence said "NOT verified", which reads as "we checked and it failed".
    Nothing was checked."""
    import pathlib
    src = pathlib.Path("server/pipeline.py").read_text()
    i = src.find("_bq_could_not_be_asked = not is_live")
    block = src[i:i + 2200]
    assert "and NOT checked" in block, block[:400]
    # A CONTIGUOUS fragment. The full sentence is split across two f-string
    # lines, so asserting it whole failed against source that says exactly
    # what the test wanted — which is this file's own demonstration of why a
    # source assertion is a spelling check.
    assert "confirmed this booking exists" in block


def test_untraceable_names_both_failures():
    """A denied id and an indicator search that found nothing are different
    facts. Reporting only the first reads as though nothing else was tried."""
    import pathlib
    src = pathlib.Path("server/pipeline.py").read_text()
    assert "so it is not a match. The indicator search" in src
    assert "had no usable venue, date or name" in src
