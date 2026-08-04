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
    assert "THE RECORD SHOWS IT" in TEXT
    assert "IT IS THIS BOOKING'S" in TEXT
    assert "IT EXPLAINS WHAT THE GUEST EXPERIENCED" in TEXT


def test_it_says_which_systems_count_as_the_backend():
    """"Check the backend" with no list is a rule the model satisfies by
    asserting it checked."""
    seg = TEXT[TEXT.index("THE RECORD SHOWS IT"):TEXT.index("IT IS THIS BOOKING'S")]
    for system in ("Zendesk", "booking record", "BMS", "experience page",
                   "fulfilment log"):
        assert system in seg, f"{system} is not named as a place to check"


def test_null_is_stated_as_a_legitimate_answer():
    """Without this the model treats null as a failure to answer and invents
    something rather than leaving it empty."""
    assert "Null is a finding" in TEXT
    assert "`operational_failure` is null" in TEXT


def test_it_says_what_an_invented_failure_costs():
    assert "worse than none" in FLAT
    assert "sends somebody to correct a person who did nothing wrong" in FLAT



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


# ── rule 6, the edge cases ──────────────────────────────────────────────────
#
# The first version had three clean conditions and six holes. Each one below
# is a case where the plain reading nulls a real failure or invents one, and
# every one of them is a shape that occurs on ordinary reviews — not exotica.

def test_an_expected_thing_missing_counts_as_a_record():
    """The most common real failure has no positive record at all: a ticket
    open four days with no agent reply, an escalation never raised, a refund
    never issued. "The backend says so" read as needing something present
    would null every one of them."""
    out = FLAT
    assert "AN EXPECTED THING MISSING IS ALSO A RECORD" in out
    assert "no agent reply" in out
    assert "escalation never raised" in out


def test_a_data_gap_is_told_apart_from_a_handling_gap():
    """The inverse, and the worse one. Zendesk not searched and CE not
    replying produce the same silence, and reading the first as the second
    invents a failure out of an outage."""
    out = FLAT
    assert "MISSING BECAUSE WE DID NOT LOOK IS NOT MISSING" in out
    assert "gap in the DATA" in out
    assert "indistinguishable unless you check which happened" in out


def test_a_failure_with_no_contact_is_still_a_failure():
    """A wrong meeting point fails a guest who never wrote in. Requiring "this
    contact" would null it — and it is entirely our fault."""
    out = FLAT
    assert "NOT EVERY FAILURE HAS A CONTACT" in out
    assert "never wrote in" in out


def test_the_third_test_is_cause_not_vocabulary():
    """"Matches the guest's claim" read as word-matching would null the case
    where they described an effect and the record shows a cause they could not
    have known about — which is most of what an RCA is for."""
    out = FLAT
    assert "IT EXPLAINS WHAT THE GUEST EXPERIENCED" in out
    assert "CAUSE, not vocabulary" in out
    assert "the guest need not have named it" in out


def test_a_failure_must_precede_what_it_caused():
    """Something we did after the review was posted did not cause the review."""
    out = FLAT
    assert "IT MUST ALSO COME FIRST" in out
    assert "after the review was posted did not cause the review" in out


def test_the_rule_says_whose_failure_it_is():
    """A vendor cancelling is a fact about the world, not an operational
    failure of ours. Without this the model files every external event as our
    failure and every RCA blames Headout."""
    out = FLAT
    assert "WHOSE FAILURE" in out
    assert "OURS — Headout's people or systems" in out
    assert "they belong in `root_cause`" in out


def test_a_specific_contact_must_be_named():
    out = FLAT
    assert "name WHICH" in out
    assert "not a citation when there are three" in out


# ── what the deleted SOP NEEDLE rule was carrying ───────────────────────────
#
# Removing the SOP compliance section took the rule that judged CE handling
# with it. Three of its four jobs were nothing to do with that section — they
# governed ce_miss and flags, which both remain — so they are back, inside
# rule 6 where they now belong.

def test_policy_not_generosity_is_stated():
    """Moved into 6b, where the DSS needle is actually read. Rule 6 keeps a
    pointer rather than a second copy — one standard, applied in three places
    (sop_gap, ce_miss, handling flags), stated once."""
    assert "AGAINST POLICY, NOT GENEROSITY" in FLAT
    assert "could have been kinder is not a miss" in FLAT
    assert "A CORRECT ACTION IS NOT A FAILURE" in TEXT, \
        "rule 6 no longer says a correct action is not a failure"
    assert "set out once in rule 6b" in FLAT, \
        "rule 6 restates the policy instead of pointing at it"





# ── rule 6 and rule 10a must not contradict each other ──────────────────────

def test_a_contactless_failure_has_somewhere_to_go():
    """Rule 6 allows an operational failure with no contact; rule 10a requires
    every FLAG to have one. Both are right, and together they would lose the
    finding unless the routing is stated."""
    out = FLAT
    assert "THIS DOES NOT MEAN THE FINDING IS LOST" in out
    assert "only `flags` that is contact-bound" in out


# ── rule 6b: sop_gap is answered from DSS, not from instinct ────────────────
#
# "+ SOP / process gap — will be if something was missed doing here. so for
# example if the guest reached out to chat asking to cancel his experience due
# to health issues, you will search dss and see what is the process and was it
# followed. if not then what was the gap."
#
# The field existed with one line of description and no method, sitting next
# to operational_failure — which a model will blur into it, because both read
# as "what went wrong on our side".

def test_the_rule_says_to_look_the_process_up():
    assert "WAS THERE A PROCESS, AND WAS IT FOLLOWED" in TEXT
    assert "Look it up before" in FLAT


def test_it_separates_sop_gap_from_operational_failure():
    """Adjacent fields on the same issue. Without the distinction stated, one
    finding gets written twice in different words and reads as two."""
    assert "`operational_failure`\n   is what a person or system DID wrong" in TEXT \
        or "is what a person or system DID wrong" in FLAT
    assert "`sop_gap` is the PROCESS that was" in FLAT
    assert "The same issue can have one, both or neither" in FLAT


def test_the_worked_example_is_the_one_that_was_asked_for():
    seg = TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT-FAILURE")]
    assert "health" in seg and "cancel" in seg
    assert "documentation" in seg


def test_a_process_that_was_followed_produces_nothing():
    """The commonest outcome, and the one a model is least likely to return
    empty unless told to."""
    seg = " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())
    assert "sop_gap is null. The process existed and was followed" in seg
    assert "Say nothing" in seg


def test_a_missing_dss_row_does_not_license_an_invented_step():
    """The same failure as rule 6's empty-needle case, in the field most
    likely to attract it: a plausible-sounding process nobody wrote down."""
    seg = " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())
    assert "Never write a step you cannot find in DSS" in seg
    assert "sent to fix a rule that does not exist" in seg


def test_an_absent_process_is_itself_a_findable_gap():
    """The other half — "no DSS path covers this" is a real finding about the
    SOP, and nulling it silently loses it."""
    seg = " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())
    assert "the ABSENCE is itself the finding" in seg
    assert "gap in the SOP rather than a gap in the handling" in seg


def test_a_deficient_process_blames_the_process_not_the_agent():
    seg = " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())
    assert "the deficiency in the process, not the agent" in seg


def test_the_data_gap_guard_applies_here_too():
    """A step recorded nowhere as done is a step not done — unless nobody
    read the record. Same trap as rule 6, and it has to be repeated here
    because this rule is read on its own."""
    seg = " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())
    assert "only if the record was actually read" in seg
    assert "gap in the DATA" in seg
    # And the sentence that carries rule 6's WHOLE evidence apparatus across.
    # Without it the two sentences below read as a local aside rather than as
    # "everything rule 6 said about evidence also governs this field", and
    # mutation testing showed the heading could be deleted with nothing
    # failing.
    assert "THE SAME EVIDENCE RULES APPLY as rule 6" in seg, \
        "6b no longer inherits rule 6's evidence rules; it only repeats two of them"


def test_the_schema_points_at_the_rule():
    """A one-line field description with a method 200 lines away is one the
    model answers from the description."""
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    assert '"sop_gap"' in schema
    assert "see rule 6b" in schema


# ── the four jobs the SOP NEEDLE rule was doing ─────────────────────────────
#
# Removing the SOP compliance section deleted the rule that judged handling.
# Three of its four jobs had nothing to do with that section — they governed
# ce_miss and flags, which both remain. They now sit in 6b, beside the DSS
# lookup they depend on, and rule 6 points at them rather than carrying a
# second copy.

def _sop_seg():
    return " ".join(TEXT[TEXT.index("6b."):TEXT.index("7. SUPPORT")].split())


def test_the_judgement_standard_says_where_it_applies():
    """One standard for three fields. Left unsaid, sop_gap gets judged one way
    and ce_miss another, on the same case."""
    seg = _sop_seg()
    assert "HOW TO JUDGE THE HANDLING" in seg
    assert "`sop_gap`, `ce_miss` and any flag" in seg


def test_job_one_policy_not_generosity():
    seg = _sop_seg()
    assert "AGAINST POLICY, NOT GENEROSITY" in seg
    assert "what a sympathetic reader would have preferred" in seg


def test_job_two_the_standing_policy_survives():
    seg = _sop_seg()
    assert "a correct denial is never a CE miss" in seg
    assert "HOC after persistence is not a deviation either" in seg


def test_job_three_a_real_deviation_is_a_closed_list():
    """"Flag real deviations" with no list is a rule the model satisfies by
    calling its own judgement real."""
    seg = _sop_seg()
    assert "WHAT A REAL DEVIATION IS. Exactly one of" in seg
    for case in ("in-policy request denied",
                 "DSS-prescribed action skipped",
                 "no policy basis and no recorded persistence"):
        assert case in seg, f"{case!r} is no longer listed"
    assert "Anything outside those three is not a deviation" in seg


def test_job_four_the_empty_needle():
    seg = _sop_seg()
    assert "match_score 0" in seg
    assert "NEVER invent a policy" in seg
    assert "scenario checklist ONLY" in seg


def test_job_four_the_social_media_fork():
    seg = _sop_seg()
    assert "every case here IS a public review" in seg
    assert "Do not treat it as unresolved" in seg


def test_rule_six_does_not_carry_a_second_copy():
    """Two statements of one standard is the arrangement that drifts. Rule 6
    points; 6b states."""
    six = TEXT[TEXT.index("6. AN OPERATIONAL FAILURE"):TEXT.index("6b.")]
    assert "STANDING POLICY" not in six
    assert "REAL deviation" not in six
    assert "rule 6b" in six
