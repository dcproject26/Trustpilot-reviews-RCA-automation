"""The rules that stop the RCA asserting things the record does not show.

Three sections of the RCA were doing the same damage in different ways:

  * `operational_failure` was being written from the guest being unhappy, or
    from a question that could not be answered — an invented failure reads as
    verified and sends somebody to correct a person who did nothing wrong;
  * `flags` were raised against teams for contacts that never happened, and a
    flag with nothing behind it costs its team the time to prove a negative;
  * the issue-specific questions were being ANSWERED into a section of their
    own, resolved from the guest's account — a claim — when the question is
    asking what the record shows. They are checks the RCA writes against now
    (§3): what one surfaces is written as an operational failure or an SOP gap,
    which is where somebody can act on it.

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

def test_the_questions_are_checks_to_write_against_not_a_section():
    """§3. The questions still reach the prompt — they are what stops the RCA
    being written past the record — but there is no answers section, so they
    have to be stated as a constraint on what IS written."""
    assert "CHECKS TO WRITE AGAINST, NOT A SECTION TO FILL IN" in TEXT
    assert "There is no `issue_specific_answers` field" in TEXT
    assert "your verdict, your root cause and your SOP gap must all be" in FLAT


def test_the_questions_are_still_carried_into_the_prompt():
    """The other half, and the one that would fail silently. §3 removed the
    SECTION, not the questions: they still route from the scenario and still
    reach the prompt. A rule about questions nobody supplies is a rule about
    nothing, and the drift it lets through has no other symptom.

    Driven with the questions the router actually produces, not a stub, so a
    routing change that stopped supplying them fails here."""
    from server.checklist import issue_questions_for
    qs = issue_questions_for(["Tickets sent late"])
    assert qs, "the router supplies no questions for a routed scenario"
    text = rca_v3_prompt(
        review_text="x", booking={"id": "1"}, timeline=[], insights={},
        dss_rec={}, l1="Operations Issue", l2="Ticket Issues",
        sub_theme="C. Ticket Delayed", support_summary="", checklist={},
        review_id="tp_1", timeline_raw=[], ticket_facts={},
        scenarios_routed=["Tickets sent late"], issue_questions=qs,
        canned_list=[])
    assert "<<ISSUE_QUESTIONS>>" not in text, "the token was never filled"
    for q in qs:
        assert q in text, f"routed question missing from the prompt: {q}"


def test_the_guest_account_alone_cannot_settle_a_question():
    assert "What the record shows settles them, not what the" in TEXT
    assert "their account is the claim, and the question asks what we can see" in FLAT


def test_what_a_question_surfaces_is_written_where_it_can_be_acted_on():
    """§3, the decided half: a check that finds something missed is assessed
    as an operational failure or an SOP gap and written THERE. Not as a trail
    line, not as a count — both of which are read and then forgotten."""
    assert "WRITE IT WHERE IT BELONGS" in TEXT
    assert "`operational_failure` if a person or system did the wrong thing" in TEXT
    assert "as its `sop_gap` if" in FLAT
    assert "Do NOT report it as an answer, a count, or a" in FLAT


def test_a_no_answer_is_not_automatically_an_operational_failure():
    """The join between the two rules. Without it the model answers No to a
    checklist question and writes an operational failure straight off it."""
    assert 'A QUESTION WHOSE ANSWER IS "NO" IS NOT AUTOMATICALLY EITHER OF THOSE' in TEXT
    assert "It becomes one only when rule 6 is satisfied" in FLAT
    assert "is a check that came back clean. Write nothing for it" in FLAT


# ── the removed sections are not asked for ──────────────────────────────────

def test_the_output_schema_no_longer_carries_the_answers_section():
    """§3. A section removed from the card but still asked for in the schema
    comes back on every run and is stored where nobody reads it — which is the
    same shape as the edit that landed in a store the reader never consults."""
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    assert '"issue_specific_answers"' not in schema
    assert '"question"' not in schema


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
    for key in ('"stated_issue"', '"what_went_wrong"',
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


def test_the_schema_says_what_sop_gap_is_for():
    """A one-line field description with a method 200 lines away is one the
    model answers from the description.

    It used to point at "rule 6b" by name. The description now states the JOB
    — why nothing caught it, the control that should have — which is what the
    model needs at the point of writing, and a cross-reference to a rule
    number survives a renumbering by pointing at the wrong rule.
    """
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    assert '"sop_gap"' in schema
    assert "why nothing caught it" in schema
    assert "the control that should have" in schema


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


# ── §2 the flag teams are the nine, and the routing rule is stated ──────────
#
# The vocabulary is enforced in code (rca_v4_validate.FLAG_TEAMS) and tested
# there. What can only live in the prompt is WHICH of the nine a finding
# belongs to, and that is the judgement the model gets wrong.

def test_the_flag_team_enum_is_the_nine():
    schema = TEXT[TEXT.index("## OUTPUT FORMAT"):]
    assert ('"team": "<GUEST | SP | CONTENT | CO | TECH | INVENTORY | PRODUCT '
            '| BIZ | FINANCE>"') in schema
    # The old eight, and OTHER, which is not a team: it is what an unreadable
    # one coerces to, and offering it invites the model to use it.
    assert '"<CE | RO | SP | CONTENT | PRODUCT | BIZ | TECH | OTHER>"' not in schema
    assert "| OTHER>" not in schema


def test_a_missing_pax_type_is_content_and_not_product():
    """HANDOFF §2, decided against a real flag: the booking flow renders
    whatever pax types the catalog defines, so a missing Baby/Infant option is
    a configuration fault. Filed as Product it goes to a team that cannot fix
    it, and the team who can never sees it."""
    seg = TEXT[TEXT.index("10-teams."):TEXT.index("10a.")]
    assert "VARIANT, PAX TYPE, INCLUSION or PAGE STATEMENT" in seg
    assert "No Baby/Infant" in seg and "is CONTENT" in seg
    assert "not a flow fault" in " ".join(seg.split())


def test_product_is_the_flow_failing_with_a_correct_catalog():
    """The other half of the same rule. Without it CONTENT swallows everything
    and the Product team is never raised at all."""
    seg = TEXT[TEXT.index("10-teams."):TEXT.index("10a.")]
    assert "THE FLOW, APP OR SITE FAILING TO DO ITS JOB WITH A CORRECT" in seg
    assert "If the catalog entry" in seg and "it is CONTENT and not this" in seg


def test_every_one_of_the_nine_is_described_not_just_listed():
    """A name with no scope is a chip the model fills by association. Each of
    the nine has to say what work it owns."""
    seg = TEXT[TEXT.index("10-teams."):TEXT.index("10a.")]
    for team in ("GUEST", "SP", "CONTENT", "CO", "TECH", "INVENTORY",
                 "PRODUCT", "BIZ", "FINANCE"):
        assert f"      {team}" in seg, f"{team} is listed without a scope"


def test_the_teams_are_tied_to_what_actions_taken_does_with_them():
    """The join is the reason the vocabulary is closed. Stated in the prompt
    because a model that does not know a team name is load-bearing treats it
    as a label."""
    seg = TEXT[TEXT.index("10-teams."):TEXT.index("10a.")]
    assert "Actions Taken is built by joining them" in seg
    assert "raises nothing at all" in " ".join(seg.split())


# ── §5 area of improvement: pointers that name their source ────────────────

def test_the_improvement_points_are_pointers_not_a_paragraph():
    assert "IS POINTERS, NOT A PARAGRAPH" in TEXT
    assert "One short pointer per array element" in TEXT


def test_every_point_must_name_the_finding_it_derives_from():
    """Provenance as a CONSTRAINT: the value is that it forces the derivation,
    so the rule has to say the point is dropped — not merely that a source is
    nice to have."""
    assert "EVERY POINT NAMES WHERE IT CAME FROM, AND THE NAME IS CHECKED" in TEXT
    assert "IS DROPPED before it renders" in TEXT
    assert "there is nowhere to put a source you do not have" in FLAT


def test_it_says_not_to_write_the_point_first_and_hunt_for_a_source():
    """The exact failure the check would otherwise produce: a real point with
    a source picked to get it past the validator."""
    assert "do not write the point first and hunt for a source afterwards" in FLAT


def test_an_empty_improvement_section_is_stated_as_the_right_answer():
    assert "EMPTY IS AN ANSWER" in TEXT
    assert "a padded section is worse than an empty one" in FLAT


def test_the_point_is_the_fix_and_the_source_is_the_fault():
    """Without this the section comes back as a restatement of the failures,
    which is the same paragraph in a different place."""
    assert "THE POINT IS THE FIX, NOT THE FAULT" in TEXT


# ── §6 a website that advertises an offer without its precondition ─────────

def _classify():
    from server.prompts import classification_prompt
    return classification_prompt("x", {}, [])


def test_an_unstated_precondition_on_an_offer_is_a_product_issue():
    """"after you buy tickets the website offers a discount on your next
    purchase; you only get it if you create an account first; Headout will not
    honour it" was coming back as Operations / Content - Misleading Info,
    because App and Website Issues was scoped to "didn't load or function"."""
    text = _classify()
    seg = text[text.index("[PRODUCT ISSUE"):text.index("[SUPPLY PARTNER ISSUE")]
    assert "ADVERTISED something without stating the condition attached to it" in seg
    assert "the site offered a discount on my next purchase" in seg
    assert "failed at the point of sale" in " ".join(seg.split())


def test_the_operations_content_rule_hands_that_case_over():
    """The priority rule puts Operations above Product and says stop at the
    first match, so a clause added only under Product would never be reached.
    The handover has to be written where the model stops."""
    text = _classify()
    ops = text[text.index("[OPERATIONS ISSUE"):text.index("[PRODUCT ISSUE")]
    assert "AN OFFER THE SITE MAKES IS NOT CONTENT ABOUT THE EXPERIENCE" in ops
    assert 'L1 = "Product Issue" / L2 = "App and Website Issues"' in ops
    assert "priority rule notwithstanding" in ops


def test_content_about_the_experience_stays_with_operations():
    """The boundary must not swallow the section it sits in: what the page says
    the tour includes is still Operations content."""
    text = _classify()
    ops = text[text.index("[OPERATIONS ISSUE"):text.index("[PRODUCT ISSUE")]
    assert "this L2 is for what we said about the EXPERIENCE" in ops
    seg = TEXT[TEXT.index("10-teams."):TEXT.index("10a.")]
    assert "an offer the site advertises without its precondition" in seg, (
        "the RCA prompt and the classifier disagree about where this lands")


# ── a refund denial does not outrank the failure that caused it ────────────
#
# The Zoomarine review: the guest was charged for a child ticket that should
# have been free, and the refund was then denied. It came back L2 = "Customer
# Support Issues" — the symptom — instead of Content/Misleading Info, which
# outranks it in the within-Operations order the ruleset already states.
#
# The order was never the problem. Nothing told the model that a refund denial
# ARISING FROM another failure is classified as that failure, so it read the
# review's headline complaint (the denial), matched the "Refund denied"
# examples verbatim, and stopped.

def test_the_priority_rule_says_a_denied_remedy_is_not_its_own_issue():
    text = _classify()
    head = text[:text.index("CLASSIFICATION RULES")]
    assert "A REMEDY REFUSED IS NOT ITS OWN ISSUE" in head, (
        "the rule has to be in the priority block, which is what the model "
        "reads before it starts matching sections")
    assert "classify the FAILURE, not the denial" in head


def test_the_priority_rule_carries_the_zoomarine_worked_example():
    text = _classify()
    head = " ".join(text[:text.index("CLASSIFICATION RULES")].split())
    assert "WORKED EXAMPLE (Zoomarine)" in head
    assert "charged for a child ticket that should have been free" in head
    assert 'L2 = "Content - Instructions not clear / Misleading Info"' in head


def test_the_rule_gives_a_test_the_model_can_apply():
    """A rule stating a principle and nothing else is a rule the model applies
    to the cases it already got right. Removing the denial and asking what is
    left is mechanical."""
    head = " ".join(_classify().split())
    assert "TEST: remove the denial from the review." in head
    assert "If a complaint remains, that complaint is the L2." in head


def test_the_boundary_is_repeated_where_the_model_actually_stops():
    """Same lesson as the offer-precondition rule: the priority block is read
    once and the section is read at the moment of decision. The "Refund
    denied" examples under Customer Support Issues are what matched the
    Zoomarine review, so the boundary has to be there too."""
    text = _classify()
    cs = text[text.index('→ L2 = "Customer Support Issues"'):
              text.index('→ L2 = "Inventory Listing Issue"')]
    assert 'STOP BEFORE USING "Refund denied"' in cs
    assert "charged for something that should have been free" in cs
    assert "Content - Instructions not clear / Misleading Info" in cs
    assert "EXAMPLE (Zoomarine)" in cs


def test_customer_support_issues_keeps_the_cases_that_are_really_its_own():
    """The boundary must not empty the L2 it sits in. A support failure that
    IS the complaint stays here, or every unanswered email starts hunting for
    an underlying cause that does not exist."""
    text = _classify()
    head = " ".join(text[:text.index("CLASSIFICATION RULES")].split())
    assert ("Customer Support Issues is for a support failure that IS the "
            "complaint" in head)
    assert "rudeness, no reply" in head
    cs = " ".join(text[text.index('→ L2 = "Customer Support Issues"'):
                       text.index('→ L2 = "Inventory Listing Issue"')].split())
    assert "ONLY when the refusal itself is the whole complaint" in cs


def test_an_external_event_refusal_is_still_customer_support():
    """The existing Force Majeure boundary sends "we refused to refund after
    your flight was cancelled" to Customer Support Issues, and it is right:
    nothing of ours failed first. The new rule must not contradict it."""
    text = _classify()
    head = " ".join(text[:text.index("CLASSIFICATION RULES")].split())
    assert "not an external event" in head
    assert "stays Customer Support Issues" in head
    fm = text[text.index('→ L2 = "Force Majeure"'):]
    assert "Operations / Customer Support Issues" in fm, (
        "the force-majeure boundary was removed or reworded — the two rules "
        "have to keep agreeing about the case they share")
