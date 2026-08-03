"""The rules that stop the RCA asserting things the record does not show.

Three sections of the RCA were doing the same damage in different ways:

  * `operational_failure` was being written from the guest being unhappy, or
    from a question that could not be answered — an invented failure reads as
    verified and sends somebody to correct a person who did nothing wrong;
  * `flags` were raised against teams for contacts that never happened, and a
    flag with nothing behind it costs its team the time to prove a negative;
  * `issue_specific_answers` were resolved from the guest's account, which is
    a claim, when the question is asking what the record shows.

The rules are in the prompt because that is where they act. They are checked
here because a rule silently deleted from a prompt has no other symptom —
output quality drifts and nothing goes red.

Content assertions on a PROMPT are not the spelling check CLAUDE.md forbids:
the prompt is data, its text is the deliverable, and there is no reachability
to be wrong about. The thing to avoid is asserting a rule exists while the
code that would enforce it does not run — and none of these are enforced in
code, by design. The model is the only enforcement, so the prompt is the
artefact.
"""
import pytest

from server.prompts import rca_v3_prompt


def _prompt():
    return rca_v3_prompt(
        review_text="x", booking={"id": "1"}, timeline=[], insights={},
        dss_rec={}, l1="Operations Issue", l2="Ticket Issues",
        sub_theme="C. Ticket Delayed", support_summary="", checklist={},
        review_id="tp_1", timeline_raw=[], ticket_facts={},
        scenarios_routed=["Tickets sent late"], issue_questions=["Q?"],
        canned_list=[])


TEXT = _prompt()
FLAT = " ".join(TEXT.split())


# ── operational_failure must be shown, not inferred ─────────────────────────

def test_the_rule_names_the_three_things_that_must_line_up():
    assert "AN OPERATIONAL FAILURE IS SOMETHING THE RECORD SHOWS" in TEXT
    assert "THE BACKEND SAYS SO" in TEXT
    assert "IT MATCHES THE CONTEXT" in TEXT
    assert "IT MATCHES THE GUEST'S CLAIM" in TEXT


def test_it_says_which_systems_count_as_the_backend():
    """"Check the backend" with no list is a rule the model satisfies by
    asserting it checked."""
    seg = TEXT[TEXT.index("THE BACKEND SAYS SO"):TEXT.index("IT MATCHES THE CONTEXT")]
    for system in ("Zendesk", "booking record", "BMS", "experience page"):
        assert system in seg, f"{system} is not named as a place to check"


def test_null_is_stated_as_a_legitimate_answer():
    """Without this the model treats null as a failure to answer and invents
    something rather than leaving it empty."""
    assert "Null is a finding" in TEXT
    assert "`operational_failure` is null" in TEXT


def test_it_says_what_an_invented_failure_costs():
    assert "worse than none" in FLAT
    assert "sends somebody to correct a person who did nothing wrong" in FLAT


def test_a_correct_denial_is_still_not_a_miss():
    """Carried over from the removed SOP rule. Without it the model writes up
    correct behaviour as a failure, which is the same damage in reverse."""
    assert "a correct denial is never a CE miss" in FLAT
    assert "HOC after persistence is not a deviation" in FLAT


# ── flags sit on a real support interaction ─────────────────────────────────

def test_every_flag_must_sit_on_a_contact_that_happened():
    assert "EVERY FLAG MUST SIT ON A SUPPORT INTERACTION THAT ACTUALLY HAPPENED" in TEXT
    assert "`zd_ref` carries the ticket" in TEXT


def test_the_rule_names_what_is_not_a_flag():
    """A rule that only says what qualifies leaves every borderline case to
    the model's judgement, and the borderline cases are the whole problem."""
    seg = TEXT[TEXT.index("EVERY FLAG MUST SIT"):]
    assert seg.count("NOT a flag") >= 4, (
        "the rule no longer enumerates what does not qualify")
    for phrase in ("No contact, no flag",
                   "general process improvement",
                   "different booking",
                   "guest's dissatisfaction"):
        assert phrase in seg, f"{phrase!r} is no longer excluded"


def test_it_says_where_a_non_flag_belongs_instead():
    """Telling the model not to emit something, with nowhere to put it, gets
    it emitted anyway under a different name."""
    seg = TEXT[TEXT.index("EVERY FLAG MUST SIT"):]
    assert "`area_of_improving`" in seg


# ── issue answers come from the record ──────────────────────────────────────

def test_each_answer_must_name_the_record_it_rests_on():
    assert "ANSWER EACH ONE FROM THE BACKEND, and say which" in TEXT
    assert "`source` is the system you checked and `ref` is the row in it" in TEXT


def test_unknown_is_stated_as_the_right_answer_when_unsettled():
    assert '"Unknown" is a legitimate answer and the RIGHT one' in TEXT


def test_the_guest_account_alone_cannot_settle_a_question():
    assert "Never resolve a question from the guest's account alone" in TEXT
    assert "what they say happened is a claim" in FLAT


def test_a_no_answer_is_not_automatically_an_operational_failure():
    """The join between the two rules. Without it the model answers No to a
    checklist question and writes an operational failure straight off it."""
    assert 'A "No" ON A QUESTION IS NOT AUTOMATICALLY AN OPERATIONAL FAILURE' in TEXT
    assert "It becomes one only when rule 6 is satisfied" in FLAT
    assert "Do not carry it into `operational_failure`" in TEXT


# ── the removed sections are not asked for ──────────────────────────────────

def test_the_prompt_no_longer_asks_for_tldr_or_sop():
    """A section removed from the schema but still described in the rules gets
    returned anyway and then dropped by the validator: tokens spent on output
    nobody sees."""
    assert "tldr" not in TEXT
    assert "TL;DR" not in TEXT
    assert "sop_compliance" not in TEXT
    assert "SOP NEEDLE" not in TEXT


def test_the_output_schema_has_no_removed_keys():
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    for key in ('"tldr"', '"sop_compliance"', '"our_mistake"', '"our_fix"'):
        assert key not in schema, f"{key} is still in the output schema"


def test_the_sections_that_remain_are_still_asked_for():
    """The other half — a removal that took a neighbour with it would show up
    as an empty card section rather than an error."""
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    for key in ('"stated_issue"', '"what_went_wrong"', '"issue_specific_answers"',
                '"support_interaction_notes"', '"sp_interaction_notes"',
                '"booking_logs"', '"flags"', '"area_of_improving"',
                '"resolution"', '"suggested_response"', '"takedown"', '"dss"'):
        assert key in schema, f"{key} went missing from the output schema"
