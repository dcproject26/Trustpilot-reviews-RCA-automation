"""RCA-content cleanup asked for from the Slack-post preview.

  * Resolution records what the guest ACTUALLY received — the model cannot know
    it — so it is left blank in the projection; the associate types it.
  * The escalation email is withheld from the model entirely (see
    test_sp_escalation_email.py for _readable_booking).
  * A "wrong policy applied" / AI-bot-miss finding is only allowed when it can
    cite the SOP or DSS rule it broke; the model is not told our automation
    rules, so an unbacked one is a guess it must not make.
"""
from server.services.rca_v4_validate import validate


def test_validate_leaves_resolution_blank_even_when_the_model_returned_one():
    """The model's resolution must not survive validation into the draft — it is
    what the guest actually received, which the model cannot know."""
    rca, _ = validate({"resolution": "Full refund of EUR 84.",
                       "what_went_wrong": {"guest_issues": []}})
    assert not rca.get("resolution"), \
        f"the model's resolution reached the draft: {rca.get('resolution')!r}"


def test_validation_never_invents_a_resolution():
    rca, _ = validate({"what_went_wrong": {"guest_issues": []}})
    assert not rca.get("resolution")


# ── the prompt guardrail on unbacked policy-miss claims ─────────────────────
#
# Prompt CONTENT check (this repo tests assembled-prompt text via _assembled()
# in test_gaps_survive_storage.py; there is no runtime harness for model
# behaviour). It guards that the instruction is present and cannot silently
# regress; whether the model obeys it is confirmed on a live re-run.

def _assembled():
    from server import prompts
    return " ".join(prompts.rca_v3_prompt(
        review_text="x", booking={}, timeline=[], insights={}, dss_rec={},
        l1="", l2="", sub_theme="", support_summary="", checklist={},
        review_id="r1").split())


def test_the_prompt_forbids_a_policy_miss_without_the_rule_it_broke():
    out = _assembled()
    assert "WRONG POLICY APPLIED" in out
    # it must tie the claim to a DSS/SOP citation and disclaim knowledge of our
    # internal automation rules
    assert "name the specific DSS needle line or SOP" in out
    assert "you cannot see" in out


def test_the_prompt_no_longer_feeds_the_escalation_email_examples():
    """The escalationEmail source-ref examples went with the field. Negative
    assertion: the removed text appears nowhere in the assembled prompt."""
    out = _assembled()
    assert "escalationEmail field is empty" not in out
    assert "read `escalation_email_status`" not in out
