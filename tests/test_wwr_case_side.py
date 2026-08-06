"""The review is the ending of the story. The Zendesk case is the story.

THE BHAYANI CASE. A guest asked to move their booking, was refused under
policy, and wrote a review about "strict policy and unhelpful support". The
RCA said the guest's claim was Inaccurate and showed nothing else. The
modification request they actually made never appeared anywhere on the card.

FOUR RULES PRODUCED THAT, and none of them was the model being careless:

  * an issue was DEFINED as "a distinct complaint in the review", so the case
    could not supply one;
  * anything found only in the tickets was routed to `flags` and then
    explicitly barred from guest_issues;
  * the fill order was claim-first, so the issue was fixed before any record
    was opened;
  * and `claim_accuracy` of Inaccurate or Unknown NULLED root_cause,
    operational_failure, sop_gap and fix outright.

The last one is the mechanical cause. "The public claim is inaccurate AND the
booking had a real problem" is a normal case and it could not be expressed.

THE VERDICT NOW JUDGES THE REVIEW'S CLAIM AND NOTHING MORE. Whether there is a
diagnosis is decided by whether the CASE shows something.
"""
import pytest

from server.services.rca_v4_validate import validate

BASE = {"l1": "Operations Issue", "l2": "Ticket Issues", "flags": [],
        "takedown": {"verdict": "No"}}


def _run(**issue):
    base = {"issue": "policy too strict", "claim": "the policy is too strict",
            "claim_accuracy": "Inaccurate", "claim_accuracy_note": "checked"}
    base.update(issue)
    out, notes = validate({**BASE,
                           "what_went_wrong": {"guest_issues": [base]}}, [])
    return out["what_went_wrong"]["guest_issues"][0], notes


# ── the case keeps the diagnosis alive ─────────────────────────────────────

def test_an_inaccurate_claim_with_a_real_case_keeps_its_diagnosis():
    """THE BHAYANI FIX. The guest's public claim did not hold; the booking
    still had a real problem, and it is the reason they wrote the review."""
    got, _ = _run(case_side="guest asked to move to 14 Aug; refused under the "
                            "24h rule; no alternative offered",
                  root_cause="modification requested inside the 24h window "
                             "and refused")
    assert got["root_cause"], got
    assert got["case_side"], got


def test_that_judgement_is_announced():
    """A diagnosis sitting beside a verdict that says the guest was wrong
    needs explaining, and the reason those coexist is the case."""
    _, notes = _run(case_side="guest asked to move; refused",
                    root_cause="refused under the 24h rule")
    said = " ".join(notes)
    assert "different questions" in said, notes


def test_an_inaccurate_claim_with_NOTHING_in_the_case_still_clears():
    """The rule the old behaviour was right about: a root cause under a claim
    that does not hold, with nothing behind it, is the shape of thoroughness
    and somebody acts on it."""
    got, notes = _run(case_side=None, root_cause="something invented")
    assert got["root_cause"] is None, got
    assert any("nothing to diagnose" in n for n in notes), notes


def test_an_accurate_claim_keeps_its_diagnosis_with_or_without_a_case():
    for case in (None, "guest chased twice"):
        got, _ = _run(claim_accuracy="Accurate", case_side=case,
                      root_cause="the voucher batch was late")
        assert got["root_cause"], (case, got)


@pytest.mark.parametrize("acc", ["Inaccurate", "Unknown"])
def test_both_undiagnosable_verdicts_behave_the_same_way(acc):
    kept, _ = _run(claim_accuracy=acc, case_side="guest asked; refused",
                   root_cause="x")
    dropped, _ = _run(claim_accuracy=acc, case_side=None, root_cause="x")
    assert kept["root_cause"] == "x", (acc, kept)
    assert dropped["root_cause"] is None, (acc, dropped)


# ── both sides are carried, and null on either is a finding ────────────────

def test_both_sides_reach_the_card():
    got, _ = _run(review_side="called the policy strict",
                  case_side="asked to move; refused")
    assert got["review_side"] == "called the policy strict"
    assert got["case_side"] == "asked to move; refused"


def test_an_issue_the_review_never_mentioned_is_allowed():
    """The case surfaced it and the guest never wrote about it publicly. That
    used to be impossible: an issue was defined as a complaint in the review."""
    got, _ = _run(claim=None, review_side=None,
                  case_side="tickets were reissued twice without telling them",
                  root_cause="duplicate fulfilment")
    assert got["review_side"] is None
    assert got["case_side"], got
    assert got["root_cause"], "an issue with no review side lost its diagnosis"


def test_an_issue_with_no_case_is_allowed_too():
    """They never contacted support. The review IS the case, and it is an
    open-and-shut one."""
    got, _ = _run(claim_accuracy="Accurate",
                  review_side="says the tour never ran", case_side=None,
                  root_cause="the departure was cancelled")
    assert got["case_side"] is None
    assert got["root_cause"], got


def test_the_fields_survive_being_absent_entirely():
    """Drafts written before these existed must not crash the validator."""
    got, _ = _run()
    assert got["review_side"] is None and got["case_side"] is None


# ── the structure the user specified ───────────────────────────────────────

def test_the_structure_is_four_headings():
    from server.checklist import WHAT_WENT_WRONG_STRUCTURE
    from server.services.wwr_post import headings
    assert len(WHAT_WENT_WRONG_STRUCTURE) == 4
    assert headings() == ["1. Guest issue", "2. Is the guest's claim accurate?",
                          "3. What actually happened?", "4. Fixes"]


def test_supply_partner_escalation_is_no_longer_a_heading():
    """It is not dropped — sp_interaction_notes carries whether it was raised,
    why not when it was not, and what came back. A mandatory heading repeating
    that printed "Did CE escalate to SP? Not recorded" under every issue on
    every card, including the many with no supply partner involved."""
    from server.services.wwr_post import headings, compose
    assert not any("escalation" in h.lower() for h in headings())
    post = compose({"guest_issues": [{"issue": "x", "claim_accuracy": "Accurate",
                                      "claim_accuracy_note": "y",
                                      "root_cause": "z"}],
                    "sp_escalation": {"escalated": "No",
                                      "reason_if_not": "SP on DND"}})
    assert "escalat" not in post.lower(), post


def test_the_composer_refuses_rather_than_guessing_if_the_structure_changes():
    """It indexes heads[0..3]. A structure that grew or shrank would index
    silently wrong, and a post with a missing heading looks like a model that
    had nothing to say under it."""
    from server.services import wwr_post
    assert wwr_post.MANDATED_HEADINGS == 4
