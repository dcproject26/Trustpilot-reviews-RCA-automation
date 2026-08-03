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

from server.tiers import (CANDIDATES, IDENTIFIED, PROCESSING, SENT,
                          UNTRACEABLE, TAB_TO_BUCKET, classify, is_unverified,
                          processing_state, tier_label)


def R(status="new"):
    return NS(status=status)


def D(**kw):
    base = dict(match_tier=None, candidate_state=False, selected_candidate_bid=None,
                booking=None, candidates_list=[])
    base.update(kw)
    return NS(**base)


CASES = [
    # (name, review, draft, expected bucket)
    # No draft row is PROCESSING, not untraceable. Untraceable is a RESULT —
    # we searched and found nothing — and it is only reachable by a run that
    # got as far as writing a draft, at step 5b. A review that has just been
    # ingested has not been searched, and calling that untraceable made
    # "we are still working" and "we looked and failed" the same tab, named
    # after the second one.
    ("no draft at all",
     R(), None, PROCESSING),
    ("draft with nothing — searched, found nothing",
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


def test_a_sent_review_is_never_bulk_reprocessed():
    """A re-run rewrites the RCA. Doing that to a review whose reply already
    went to the guest, and flipping its status back to draft, would drag it out
    of Sent as if nothing had been sent."""
    import server.api as api
    src = open(api.__file__).read()
    i = src.index("if scope == \"incomplete\":")
    block = src[i:i + 500]
    assert 'r.status == "sent"' in block, (
        "the incomplete scope no longer excludes sent reviews")


def test_the_pipeline_does_not_downgrade_a_sent_review():
    import server.pipeline as P
    src = open(P.__file__).read()
    assert 'if review.status != "sent":' in src, (
        "a re-run sets status back to draft unconditionally, which pulls a "
        "sent review out of the Sent tab")


# ── processing is not untraceable ───────────────────────────────────────────
#
# "all the new reviews are getting populated in untraceable tab again after
# clicking on refresh to slack". They were not untraceable. The draft row is
# written at step 5b, after BID extraction and the BigQuery search, so from
# ingest until then a review has no draft — and classify() filed that as
# UNTRACEABLE. Queue fifteen reviews and all fifteen appear in Untraceable at
# once, draining out as their runs finish.
#
# Untraceable is a RESULT. Reaching it requires having looked.

def test_a_review_that_has_not_been_searched_is_not_untraceable():
    assert classify(R(), None) == PROCESSING
    assert classify(R(), None) != UNTRACEABLE


def test_a_review_that_was_searched_and_missed_still_is():
    """The other half. Widening this would hide real misses in a tab nobody
    treats as a problem."""
    assert classify(R(), D()) == UNTRACEABLE
    assert classify(R(), D(match_tier=1, booking={})) == UNTRACEABLE


def test_the_two_read_differently_on_the_card():
    a = processing_state(R(), None)
    b = processing_state(R(), D())
    assert a[0] and a[1], "a review with no draft says nothing about its state"
    assert b == ("", ""), "a draft that WAS searched is not in a processing state"


def test_a_run_in_flight_says_wait_not_re_run(monkeypatch):
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_x",
                        {"step": 3, "total": 8, "stage": "Zendesk",
                         "started_at": 0, "elapsed_s": 4})
    state, why = processing_state(NS(id="tp_x", status="new"), None)
    assert state == "running"
    assert "Step 3 of 8" in why and "Zendesk" in why
    assert "not a failed match" in why


def test_a_run_that_died_says_re_run_it():
    """The draft is saved before anything that can fail, so no draft row and
    no run in progress is a BUG — not a booking we could not find."""
    state, why = processing_state(NS(id="tp_gone", status="new"), None)
    assert state == "stalled"
    assert "Re-run it" in why
    assert "not a booking we could not find" in why


def test_the_two_states_do_not_read_the_same(monkeypatch):
    import server.pipeline as P
    monkeypatch.setitem(P.PIPELINE_PROGRESS, "tp_live",
                        {"step": 1, "total": 8, "stage": "BID",
                         "started_at": 0, "elapsed_s": 1})
    live = processing_state(NS(id="tp_live", status="new"), None)
    dead = processing_state(NS(id="tp_dead", status="new"), None)
    assert live[0] != dead[0] and live[1] != dead[1]


def test_a_sent_review_with_no_draft_is_still_sent():
    """Sent outranks everything, including this."""
    assert classify(R(status="sent"), None) == SENT


def test_processing_has_a_tab_of_its_own():
    assert "processing" in TAB_TO_BUCKET
    assert TAB_TO_BUCKET["processing"] == PROCESSING
    assert TAB_TO_BUCKET["untraceable"] == UNTRACEABLE
