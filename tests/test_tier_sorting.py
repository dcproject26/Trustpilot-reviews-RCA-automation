"""Every state a review can be in, and the one tab it belongs to.

This logic used to live in two places with different wording, and three
mistakes came out of that: a CONFIRMED candidate stayed under "possible
matches", a Tier 1 booking found via Zendesk was filed under "possible
matches" with nothing to pick, and the API and dashboard could disagree about
the same review. The table below is exhaustive on purpose - a sorting rule
that is right for the common case and wrong for two others is not a rule.
"""
from types import SimpleNamespace as NS

import pytest

from server.tiers import (CANDIDATES, IDENTIFIED, SENT, UNTRACEABLE,
                          TAB_TO_BUCKET, classify, is_unverified, tier_label)


def R(status="new"):
    return NS(status=status)


def D(**kw):
    base = dict(match_tier=None, candidate_state=False, selected_candidate_bid=None,
                booking=None, candidates_list=[])
    base.update(kw)
    return NS(**base)


CASES = [
    # (name, review, draft, expected bucket)
    ("no draft at all",
     R(), None, UNTRACEABLE),
    ("draft with nothing",
     R(), D(), UNTRACEABLE),

    ("tier 1 from the review's own BID",
     R(), D(match_tier=1, booking={"id": "329"}), IDENTIFIED),
    ("tier 1 found via Zendesk, no BID in the review text",
     R(), D(match_tier=1, booking={"id": "329"}), IDENTIFIED),
    ("tier 1 unverified fallback (BigQuery could not confirm it)",
     R(), D(match_tier=1, booking={"id": "329", "_unverified": True}), IDENTIFIED),

    ("shortlist open, nobody has picked",
     R(), D(match_tier=2, candidate_state=True,
            candidates_list=[{"id": "1"}, {"id": "2"}]), CANDIDATES),
    ("shortlist open AND a provisional booking attached",
     R(), D(match_tier=2, candidate_state=True, booking={"id": "guess"},
            candidates_list=[{"id": "1"}]), CANDIDATES),
    ("candidates stored but the flag was never set",
     R(), D(match_tier=2, candidate_state=False,
            candidates_list=[{"id": "1"}]), CANDIDATES),

    ("associate CONFIRMED a candidate",
     R(), D(match_tier=2, candidate_state=False, selected_candidate_bid="329",
            booking={"id": "329"}, candidates_list=[{"id": "329"}]), IDENTIFIED),
    ("confirmed, but the flag was left on",
     R(), D(match_tier=2, candidate_state=True, selected_candidate_bid="329",
            booking={"id": "329"}), IDENTIFIED),

    ("sent beats everything",
     R(status="sent"), D(match_tier=1, booking={"id": "329"}), SENT),
    ("sent with no draft",
     R(status="sent"), None, SENT),
    ("sent while a picker was open",
     R(status="sent"), D(candidate_state=True, candidates_list=[{"id": "1"}]), SENT),

    ("booking dict present but empty",
     R(), D(match_tier=1, booking={}), UNTRACEABLE),
    ("booking id empty string",
     R(), D(match_tier=1, booking={"id": ""}), UNTRACEABLE),
]


@pytest.mark.parametrize("name,review,draft,expected",
                         CASES, ids=[c[0] for c in CASES])
def test_bucket(name, review, draft, expected):
    assert classify(review, draft) == expected


def test_every_case_lands_in_exactly_one_tab():
    """No review may appear under two tabs, or under none."""
    for name, review, draft, _ in CASES:
        hits = [tab for tab, bucket in TAB_TO_BUCKET.items()
                if classify(review, draft) == bucket]
        assert len(hits) == 1, f"{name!r} landed in {hits}"


def test_confirming_a_candidate_moves_it_out_of_candidates():
    """The regression that started this: confirm a candidate and the card
    stayed under possible matches."""
    d = D(match_tier=2, candidate_state=True, candidates_list=[{"id": "329"}])
    assert classify(R(), d) == CANDIDATES
    d.selected_candidate_bid = "329"
    d.candidate_state = False
    d.booking = {"id": "329"}
    assert classify(R(), d) == IDENTIFIED, "a confirmed booking is identified"


def test_a_provisional_booking_is_never_shown_as_identified():
    """A Tier 2 shortlist can carry a provisional booking. Until a human picks,
    presenting it as identified would show a guess as a fact."""
    d = D(match_tier=2, candidate_state=True, booking={"id": "maybe"},
          candidates_list=[{"id": "maybe"}, {"id": "other"}])
    assert classify(R(), d) == CANDIDATES


def test_tier_label_and_unverified_flag():
    assert tier_label(D(match_tier=1)) == "T1"
    assert tier_label(D(match_tier=2)) == "T2"
    assert tier_label(D()) == "—"
    assert tier_label(None) == "—"
    assert is_unverified(D(booking={"id": "1", "_unverified": True})) is True
    assert is_unverified(D(booking={"id": "1"})) is False
    assert is_unverified(D()) is False
