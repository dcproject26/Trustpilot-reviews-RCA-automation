"""The cascade stopped at the first step that returned ANYTHING.

A review naming the Eiffel Tower was matched to the guest's Sphere, Colosseum
and cruise bookings — found by their Zendesk tickets, every one of them scored
venue 0, and shown under a trail line that said in words "no venue matched,
these are weak". The venue+date search that would have asked the right
question sat behind `if not cascade_done:` and never ran, because the Zendesk
step set `cascade_done = True` on its way past.

Two decisions come out of that, and both are tested by driving them rather
than by reading pipeline.py:

  1. WHEN to continue past a shortlist that already returned rows
  2. WHAT the picker shows if continuing turns out to find nothing

Plus the third thing that was missing: a venue+date step that returns exactly
one row promotes it to the confirmed booking having never looked at the guest.
`verify_identifiers` is what looks.
"""
import pytest

from server.pipeline import venue_fallthrough, shortlist_restore
from server.services.bigquery import verify_identifiers


# ── 1. when the cascade continues ───────────────────────────────────────────

def test_a_venue_mismatched_shortlist_does_not_end_the_cascade():
    """The bug, stated directly: tgids resolved, nothing matched them."""
    assert venue_fallthrough(["4001", "4002"], False) is True


def test_a_shortlist_that_matched_the_venue_is_not_second_guessed():
    """Step 3 asks a WEAKER question than a shortlist that already agrees on
    venue — it has no guest at all. Continuing here would replace a good
    answer with a worse one."""
    assert venue_fallthrough(["4001"], True) is False


def test_no_venue_to_search_on_means_there_is_nothing_to_continue_to():
    """Both halves are load-bearing. With no tgids, 3a and 3b have no venue
    filter and 3c is gated on tgids too, so continuing costs the shortlist and
    buys nothing."""
    assert venue_fallthrough([], False) is False
    assert venue_fallthrough(None, False) is False


# ── 2. what survives when the continuation finds nothing ────────────────────

def test_the_shortlist_comes_back_when_step_3_finds_nothing():
    """An empty picker is not a neutral outcome. It reads as "this guest has
    no bookings", which is a stronger and falser claim than three weak ones."""
    saved = [{"id": "b1"}, {"id": "b2"}]
    restore, cands, state = shortlist_restore(False, True, saved, True)
    assert restore is True
    assert cands == saved
    assert state is True


def test_step_3_finding_something_keeps_it():
    """`cascade_done` True means a later step answered. That answer wins —
    restoring over it is the original bug with the steps swapped."""
    assert shortlist_restore(True, True, [{"id": "b1"}], True)[0] is False


def test_nothing_is_restored_when_the_cascade_never_fell_through():
    """A run that reached Step 3 by the ordinary route has no put-aside
    shortlist, and inventing one would show bookings from a search that was
    never run for this review."""
    assert shortlist_restore(False, False, [{"id": "b1"}], True)[0] is False


def test_an_empty_saved_shortlist_is_not_restored_over_untraceable():
    """Restoring an empty list would set cascade_done and skip Untraceable,
    so the run would end saying nothing at all rather than saying it found
    nothing — the exact difference this codebase keeps losing."""
    assert shortlist_restore(False, True, [], False)[0] is False
    assert shortlist_restore(False, True, None, None)[0] is False


# ── 3. the identifiers on the booking Step 3 acquires ───────────────────────

def _bk(name="", visit="2026-10-20"):
    return {"id": "b1", "primary_guest_name": name, "date_of_visit": visit}


IND = {"dates_mentioned": ["2026-10-20"]}


def test_a_matching_guest_name_is_reported_as_checked_and_agreed():
    v = verify_identifiers(_bk("Fredrik Olsen"), IND, "Fredrik", "Olsen")
    assert v["verdict"] == "match"
    assert any("guest name" in a for a in v["agreed"]), v


def test_a_different_guest_name_is_a_mismatch():
    v = verify_identifiers(_bk("Marta Kowalska"), IND, "Fredrik", "Olsen")
    assert v["verdict"] == "mismatch"
    assert any("guest name" in d for d in v["disagreed"]), v


def test_a_hashed_guest_name_is_unchecked_and_never_a_match():
    """639,109 of 639,109 bookings behind a support contact store a hash. If
    that came back "match" the caller would auto-promote reporting a guest
    check that never happened; if it came back "mismatch" every one of them
    would be demoted. It is neither, and it says which."""
    v = verify_identifiers(_bk("jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8="),
                           {}, "Fredrik", "Olsen")
    assert v["verdict"] == "unchecked", v
    assert any("PII hash" in u for u in v["uncheckable"]), v


def test_a_missing_guest_name_says_so_rather_than_disagreeing():
    v = verify_identifiers(_bk(""), {}, "Fredrik", "Olsen")
    assert v["verdict"] == "unchecked"
    assert any("records none" in u for u in v["uncheckable"]), v


def test_an_anonymous_review_cannot_check_the_name_either():
    """No author name is a fact about the REVIEW, not about the booking, and
    it must not read as the booking failing a check."""
    v = verify_identifiers(_bk("Fredrik Olsen"), {}, None, None)
    assert not v["disagreed"]
    assert any("no author name" in u for u in v["uncheckable"]), v


def test_a_first_name_only_agreement_is_agreement_not_a_mismatch():
    """Married names, a booking under a partner's name. The score is 0.3 and
    the line says which part agreed, so the reader can judge it."""
    v = verify_identifiers(_bk("Fredrik Rostvold"), {}, "Fredrik", "Olsen")
    assert v["verdict"] == "match"
    assert any("first name only" in a for a in v["agreed"]), v


def test_the_visit_date_agrees_when_the_review_named_it():
    v = verify_identifiers(_bk("Fredrik Olsen", "2026-10-20"), IND,
                           "Fredrik", "Olsen")
    assert any("visit date" in a for a in v["agreed"]), v


def test_a_differing_visit_date_is_recorded_but_does_not_demote_alone():
    """`dates_mentioned` is whatever date the review named — often the booking
    date or the date they were emailed. Demoting on it would demote correct
    matches, so it is reported and the judgement is stated in the line."""
    v = verify_identifiers(_bk("Fredrik Olsen", "2026-10-22"), IND,
                           "Fredrik", "Olsen")
    assert v["verdict"] == "match", v
    assert any("visit date" in d for d in v["disagreed"]), v
    assert any("often names the booking date" in d for d in v["disagreed"]), v


def test_a_date_only_disagreement_never_reaches_mismatch():
    """With the name uncheckable and only the date differing, the verdict must
    not be "mismatch" — that would demote every hashed-name booking whose
    review happened to name its booking date."""
    v = verify_identifiers(_bk("jVwe+fjfm48WSok1xEK+I/8fnIoV+kY8P8z7xxk+NM8=",
                               "2026-10-22"), IND, "Fredrik", "Olsen")
    assert v["verdict"] == "unchecked", v


def test_an_unparseable_review_date_says_so_rather_than_comparing_nothing():
    """"sometime in June" passes a naive truthiness check and compares
    nothing. A silent skip here is indistinguishable from a date that agreed."""
    v = verify_identifiers(_bk("Fredrik Olsen"), {"dates_mentioned": ["sometime in June"]},
                           "Fredrik", "Olsen")
    assert any("usable form" in u for u in v["uncheckable"]), v


def test_every_identifier_lands_in_exactly_one_bucket():
    """Nothing is dropped. A field that appears in none of the three lists is
    a check that silently did not happen."""
    for bk, ind in ((_bk("Fredrik Olsen"), IND),
                    (_bk(""), {}),
                    (_bk("Marta Kowalska", ""), {"dates_mentioned": ["2026-10-20"]})):
        v = verify_identifiers(bk, ind, "Fredrik", "Olsen")
        lines = v["agreed"] + v["disagreed"] + v["uncheckable"]
        assert sum("guest name" in l for l in lines) == 1, (bk, v)
        assert sum("visit date" in l for l in lines) == 1, (bk, v)


# ── 4. the step that used to swallow the cascade ────────────────────────────

def test_the_zendesk_step_never_ends_the_cascade_on_a_literal():
    """NEGATIVE source assertion, and deliberately the only one here.

    The branch lives inside `process_review` — a coroutine wanting a database,
    Claude, Zendesk and BigQuery — so the decision was extracted to
    `venue_fallthrough` and is driven above. What driving it cannot show is
    that the call site still asks: restoring `cascade_done = True` in this
    branch reinstates the bug with every test above still green.

    Stated as "this string appears nowhere in the branch", which unreachable
    code cannot satisfy the way a positive assertion can."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.index('narrowing_path = ("zendesk_requester_candidates"')
    branch = src[i:src.index("# ── Step 3: BQ narrowing", i)]
    assert "cascade_done = True" not in branch, \
        "the Zendesk step ends the cascade on a literal again — Step 3 " \
        "cannot run, whatever venue_fallthrough returns"
