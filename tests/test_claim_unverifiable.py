"""A claim we checked and cannot settle is not a claim nobody checked.

`claim_accuracy` had four values, and "Unknown" carried both meanings:

  * we LOOKED and no record we hold can settle it — the guest says the room
    was cold, the guide was rude, the queue was two hours. Nothing in the
    booking, the tickets or Zendesk records any of those. That is FINISHED
    work.
  * we could not establish it — the lookup failed, the ticket was never
    retrieved, there was no evidence to check against. That is work
    OUTSTANDING.

One chip for both is this codebase's first rule exactly: "I ran and found
nothing" reading identically to "I did not run". It costs the reader the one
thing that decides what to do next, and the RCA looks equally complete either
way.

The asymmetry matters as much as the split. Anything unrecognised falls to
Unknown and NEVER to Unverifiable — claiming a check nobody ran is worse than
admitting a gap, because nobody goes back to a question already marked
answered.
"""
import pytest

from server.services.rca_v4_validate import CLAIM_ACCURACY, _accuracy, validate


def _issue(acc, note=None):
    i = {"issue": "x", "claim": "y", "claim_accuracy": acc}
    if note:
        i["claim_accuracy_note"] = note
    out, _ = validate({"what_went_wrong": {"guest_issues": [i]}})
    return out["what_went_wrong"]["guest_issues"][0]


def test_the_vocabulary_carries_both_answers():
    assert "Unverifiable" in CLAIM_ACCURACY
    assert "Unknown" in CLAIM_ACCURACY


@pytest.mark.parametrize("raw", [
    "Unverifiable", "unverifiable", "Not verifiable", "Cannot verify",
    "cannot verify — nothing on file", "No record of this",
])
def test_a_reached_but_unsettleable_verdict_is_kept(raw):
    assert _accuracy(raw)[0] == "Unverifiable", raw


@pytest.mark.parametrize("raw", ["Unknown", "", None, "gibberish", "TBD", "?"])
def test_anything_else_falls_to_unknown_never_to_unverifiable(raw):
    """Claiming a check nobody ran is not a safe default."""
    assert _accuracy(raw)[0] == "Unknown", raw


def test_the_two_are_not_the_same_value():
    assert _accuracy("Unverifiable")[0] != _accuracy("Unknown")[0]


def test_the_reason_survives_alongside_the_verdict():
    """An Unverifiable verdict with no reason is indistinguishable from a
    claim nobody looked at — which is the distinction it exists to draw."""
    v, tail = _accuracy("Unverifiable — nothing in Zendesk or the booking")
    assert v == "Unverifiable"
    assert "Zendesk" in (tail or "")


def test_it_reaches_the_projected_issue():
    assert _issue("Unverifiable")["claim_accuracy"] == "Unverifiable"


def test_the_existing_verdicts_are_untouched():
    """The split must not disturb the four that were already right."""
    for raw, want in (("Accurate", "Accurate"), ("Yes", "Accurate"),
                      ("Partly accurate", "Partly accurate"),
                      ("Partially True", "Partly accurate"),
                      ("Inaccurate", "Inaccurate"), ("No", "Inaccurate")):
        assert _accuracy(raw)[0] == want, raw


# ── the prompt tells the model the difference ──────────────────────────────

def test_the_prompt_offers_both_and_explains_which_is_which():
    from server.prompts import rca_v3_prompt
    t = rca_v3_prompt(
        review_text="x", booking={"id": "1"}, timeline=[], insights={},
        dss_rec={}, l1="Operations Issue", l2="Meeting Point Issues",
        sub_theme="", support_summary="", checklist={}, review_id="tp_1",
        timeline_raw=[], ticket_facts={}, scenarios_routed=[],
        issue_questions=[], canned_list=[])
    flat = " ".join(t.split())
    assert "Unverifiable" in t
    assert "UNVERIFIABLE AND UNKNOWN ARE DIFFERENT ANSWERS" in flat
    assert "That is work OUTSTANDING" in flat
    assert 'Never use Unverifiable as a fallback for "I did not look"' in flat


def test_the_prompt_gives_the_model_examples_it_cannot_check():
    """Stated abstractly, "unverifiable" gets used for anything inconvenient."""
    from server.prompts import RCA_V4_TEMPLATE
    flat = " ".join(RCA_V4_TEMPLATE.split())
    assert "the room was cold" in flat and "the queue was two hours" in flat


# ── the Slack post carries it ──────────────────────────────────────────────

def test_slack_states_why_when_the_model_gave_no_reason():
    from types import SimpleNamespace
    from server.services.slack import format_rca_slack
    v4 = {"what_went_wrong": {"guest_issues": [
        {"issue": "Rude guide", "claim": "the guide was rude",
         "claim_accuracy": "Unverifiable", "owner": "SP", "evidence": []}]},
        "flags": [], "booking_logs": [], "takedown": {"verdict": "No"}}
    d = SimpleNamespace(
        booking={"id": "1"}, rca_v3=v4, l1="Supply Partner Issue",
        l2="Seating Issues", sub_theme="", primary_scenario="", overlay_scenarios=[],
        wwr_scenarios=[], wwr_chain=[], support_interaction_frames=[],
        sp_interaction_frames=[], area_of_improving=[], actions_taken={},
        resolution="", checklist_answers=[], tldr="", insights={"_window_days": 90})
    out = format_rca_slack(SimpleNamespace(rating=1, author="A"), d)
    assert "Unverifiable" in out
    assert "no record we hold can settle" in out, \
        "an Unverifiable verdict went out with no reason beside it"


def test_slack_does_not_invent_a_reason_for_the_other_verdicts():
    from types import SimpleNamespace
    from server.services.slack import format_rca_slack
    v4 = {"what_went_wrong": {"guest_issues": [
        {"issue": "Late tickets", "claim": "they were late",
         "claim_accuracy": "Accurate", "owner": "RO", "evidence": []}]},
        "flags": [], "booking_logs": [], "takedown": {"verdict": "No"}}
    d = SimpleNamespace(
        booking={"id": "1"}, rca_v3=v4, l1="Operations Issue", l2="Ticket Issues",
        sub_theme="", primary_scenario="", overlay_scenarios=[], wwr_scenarios=[],
        wwr_chain=[], support_interaction_frames=[], sp_interaction_frames=[],
        area_of_improving=[], actions_taken={}, resolution="",
        checklist_answers=[], tldr="", insights={"_window_days": 90})
    out = format_rca_slack(SimpleNamespace(rating=1, author="A"), d)
    assert "no record we hold can settle" not in out
