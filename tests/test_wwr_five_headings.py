"""The Slack post's what-went-wrong section: five mandated headings, one composer.

Driven, not asserted against source. `compose()` is a pure function and every
guarantee below is a call to it — the point of extracting it from both
renderers was to make the format testable at all. The only source assertions
here are NEGATIVE ones (CLAUDE.md §2): that the client no longer contains its
own copy of the composer, which unreachability cannot defeat.
"""
import re

from server.checklist import WHAT_WENT_WRONG_STRUCTURE
from server.services.wwr_post import (compose, compose_legacy, headings,
                                      accuracy_line)

CLIENT = open("client/index.html", encoding="utf-8").read()


def _issue(**kw):
    base = {"issue": "Tickets never arrived", "claim_accuracy": "Accurate",
            "root_cause": "Vendor API timed out"}
    base.update(kw)
    return base


def _wwr(issues, **kw):
    node = {"guest_issues": issues}
    node.update(kw)
    return node


# ── The headings come from the checklist, and there are five ────────────────

def test_headings_are_derived_from_the_checklist_structure():
    """The wording is not spelled twice. If WHAT_WENT_WRONG_STRUCTURE is
    re-worded, the post follows it — that list is what the RCA prompt is built
    from, so a post claiming a structure the model was never asked for is the
    drift this guards."""
    assert headings() == [
        "1. Guest issue",
        "2. Is the guest's claim accurate?",
        "3. What actually happened?",
        "4. Supply Partner escalation",
        "5. Fixes",
    ]
    assert len(WHAT_WENT_WRONG_STRUCTURE) == 5


def test_all_five_headings_are_present_even_when_the_rca_is_empty():
    """Headings 1-5 are MANDATORY. An RCA that filled none of them still
    prints all five — a section that shrinks to the fields that happened to be
    populated is one a reader cannot tell from a truncated render."""
    text = compose(_wwr([{"issue": "", "claim_accuracy": ""}]))
    for h in headings():
        assert h in text, f"missing mandatory heading: {h}"


def test_a_populated_rca_still_prints_all_five_headings():
    text = compose(_wwr([_issue()], sp_escalation={"escalated": "Yes"}))
    for h in headings():
        assert h in text


# ── Heading 2: the verdict vocabulary ───────────────────────────────────────

def test_the_three_verdicts_map_to_the_users_words():
    assert accuracy_line({"claim_accuracy": "Accurate"}) == "Yes"
    assert accuracy_line({"claim_accuracy": "Partly accurate"}) == "Partially True"
    assert accuracy_line({"claim_accuracy": "Inaccurate"}) == "No"


def test_unknown_never_prints_as_one_of_the_three():
    """THE OPEN QUESTION. The validator's enum has four values and the user's
    heading-2 vocabulary has three. `Unknown` must not be coerced into "No":
    "No" is a finding — we checked and the guest was wrong — and printing it
    for a claim nobody settled puts a verdict on a guest that nobody reached.
    """
    for note in ({}, {"claim_accuracy_note": "no record covers the delivery window"}):
        line = accuracy_line({"claim_accuracy": "Unknown", **note})
        assert line not in ("Yes", "Partially True", "No")
        assert "Not established" in line


def test_the_two_kinds_of_unknown_are_told_apart():
    """A check that ran and came back empty must not read the same as a check
    that left no trace of running. The prompt requires a note on every
    verdict, so the note's ABSENCE is the signal — and the two produce
    different sentences."""
    checked = accuracy_line({"claim_accuracy": "Unknown",
                             "claim_accuracy_note": "no booking record covers this"})
    silent = accuracy_line({"claim_accuracy": "Unknown"})
    assert checked != silent
    assert "cannot settle" in checked
    assert "no reason" in silent


def test_a_missing_verdict_is_distinct_from_an_unknown_one():
    """No claim_accuracy at all is a third thing again: the RCA recorded no
    verdict, rather than recording one that could not be settled."""
    missing = accuracy_line({})
    assert missing not in ("Yes", "Partially True", "No")
    assert missing != accuracy_line({"claim_accuracy": "Unknown"})
    assert "no verdict" in missing


def test_a_verdict_outside_the_enum_is_named_not_coerced():
    """An older draft may carry a value the current enum dropped. Naming it
    lets a reader see it is outside the three; silently mapping it to one of
    them would invent a verdict."""
    line = accuracy_line({"claim_accuracy": "Unverifiable"})
    assert line not in ("Yes", "Partially True", "No")
    assert "Unverifiable" in line


# ── Empty mandatory headings say WHICH kind of empty ────────────────────────

def test_an_empty_heading_three_says_nothing_was_written_there():
    text = compose(_wwr([{"issue": "X", "claim_accuracy": "Accurate"}]))
    assert "No root cause recorded" in text


def test_heading_three_omits_only_the_indicative_subpoints_it_lacks():
    """Sub-points a/b/c are INDICATIVE: an issue with a root cause but no SOP
    gap prints the root cause and does not print an empty (c). Printing a dash
    for every absent field turns a focused block into a form with blanks."""
    text = compose(_wwr([_issue()]))
    assert "Root cause: Vendor API timed out" in text
    assert "Operational failure" not in text
    assert "SOP/process gap" not in text


def test_heading_four_says_when_no_escalation_verdict_was_recorded():
    text = compose(_wwr([_issue()]))
    assert "Not recorded" in text


def test_heading_four_carries_the_dnd_reason_when_there_is_one():
    text = compose(_wwr([_issue()],
                        sp_escalation={"escalated": "No",
                                       "reason_if_not": "SP is on DND"}))
    assert "Did CE escalate to SP? No" in text
    assert "SP is on DND" in text


def test_a_bare_no_escalation_says_the_dnd_question_went_unanswered():
    """The user asked specifically for the DND case to be stated. A "No" with
    no reason is an incomplete answer to heading 4b and says so rather than
    reading as a complete one."""
    text = compose(_wwr([_issue()], sp_escalation={"escalated": "No"}))
    assert "DND" in text


def test_heading_five_says_when_no_fix_and_no_team_exist():
    text = compose(_wwr([_issue()]))
    assert "No fix recorded" in text


def test_heading_five_tags_the_owner_and_states_the_action():
    text = compose(_wwr([_issue(fix={"action": "Add a retry",
                                     "owner": "PRODUCT",
                                     "because": "no retry on timeout"})]))
    assert "@PRODUCT" in text
    assert "Add a retry" in text


def test_a_fix_with_no_owner_says_no_team_was_tagged():
    """Heading 5 is "tag the relevant team(s)". A fix with no owner leaves
    that unanswered, and an unanswered mandatory sub-point must not look like
    a fix that needs nobody."""
    text = compose(_wwr([_issue(fix={"action": "Add a retry"})]))
    assert "No team tagged" in text


# ── The fix object is never stringified ─────────────────────────────────────

def test_the_fix_object_never_reaches_the_post_as_a_repr():
    """The defect this whole one-composer change exists to kill: the client
    concatenated the fix OBJECT into a string and "Fix: [object Object]" went
    out on a real post while the server's copy was correct."""
    text = compose(_wwr([_issue(fix={"action": "Add a retry", "owner": "CE",
                                     "because": "gap"})]))
    assert "[object Object]" not in text
    assert "{'action'" not in text
    assert "{\"action\"" not in text


# ── Several guest issues repeat the whole structure ─────────────────────────

def test_several_issues_each_repeat_all_five_headings():
    """THE OTHER OPEN QUESTION. Listing several complaints under 1a was the
    alternative, and it is the flattening this project already fixed once: two
    complaints with different root causes went into one list and the reader
    could not tell which cause belonged to which. Each issue is a
    self-contained answer to all five headings."""
    text = compose(_wwr([
        _issue(issue="Tickets never arrived", root_cause="Vendor API timed out"),
        _issue(issue="Refund was refused", root_cause="Agent applied the wrong SOP"),
    ]))
    for h in headings():
        assert text.count(h) == 2, f"{h} should appear once per issue"
    # And each cause stays attached to its own issue.
    first = text.index("Tickets never arrived")
    second = text.index("Refund was refused")
    assert first < text.index("Vendor API timed out") < second
    assert second < text.index("Agent applied the wrong SOP")


def test_several_issues_are_labelled_so_the_blocks_are_distinguishable():
    text = compose(_wwr([_issue(issue="A"), _issue(issue="B")]))
    assert "Guest issue 1 of 2" in text
    assert "Guest issue 2 of 2" in text


def test_a_single_issue_is_not_labelled_one_of_one():
    text = compose(_wwr([_issue()]))
    assert "1 of 1" not in text


# ── Everything else is OUT of the post ──────────────────────────────────────

def test_the_post_carries_nothing_but_the_five_headings():
    """Evidence rows, the verbatim guest quote, `pattern`, `backs_claim`, the
    owner chip's own line and the accuracy note's prose stay on the dashboard.
    The user asked for the five headings and nothing else."""
    text = compose(_wwr([_issue(
        claim="They told me it would arrive by 6pm and it never did",
        claim_accuracy_note="the booking record shows no delivery attempt",
        pattern="third such case this month",
        backs_claim=True,
        evidence=[{"source": "zendesk", "text": "agent promised 6pm", "ref": "ZD-1"}],
    )]))
    assert "They told me it would arrive" not in text
    assert "third such case this month" not in text
    assert "agent promised 6pm" not in text
    assert "ZD-1" not in text
    assert "the booking record shows no delivery attempt" not in text
    assert "backs_claim" not in text


def test_an_unknown_verdict_still_keeps_the_note_prose_off_the_post():
    """The note's PRESENCE decides which sentence heading 2 prints, but its
    text is not reproduced — the note is a dashboard field."""
    text = compose(_wwr([_issue(claim_accuracy="Unknown",
                                claim_accuracy_note="checked BMS and Zendesk")]))
    assert "checked BMS and Zendesk" not in text
    assert "Not established" in text


# ── Empty and absent are different ──────────────────────────────────────────

def test_no_what_went_wrong_node_at_all_composes_to_nothing():
    """The caller decides whether to print a heading for a section with no
    data; an empty string is how this says it had none."""
    assert compose(None) == ""
    assert compose({}) == ""


def test_an_issueless_node_says_so_rather_than_printing_bare_headings():
    """A what_went_wrong that exists but lists no guest issue is a different
    fact from one that was never written, and the reader is told which."""
    text = compose({"guest_issues": [], "sp_escalation": {"escalated": "Yes"}})
    assert "No guest issue was recorded" in text
    for h in headings():
        assert h in text


def test_a_prev4_document_level_analysis_is_folded_into_heading_three():
    """A pre-v4 draft keeps its root causes under `what_happened` and has no
    guest_issues. Printing "no root cause recorded" over an RCA that recorded
    three would be the inverse bug: a healthy run made to look faulty."""
    text = compose({
        "guest_issues": [],
        "what_happened": {
            "root_causes": [{"classification": "ops", "issue": "Late entry",
                             "cause": "queue was mismanaged"}],
            "sop_gap": [{"point": "no queue SOP exists"}],
        },
    })
    assert "queue was mismanaged" in text
    assert "no queue SOP exists" in text
    assert "No root cause recorded" not in text


def test_document_level_points_are_never_rendered_as_dict_reprs():
    text = compose({
        "guest_issues": [],
        "what_happened": {"operational_failure": [{"point": "no callback made"}]},
    })
    assert "no callback made" in text
    assert "{'point'" not in text


# ── The legacy shape goes through the SAME composer ─────────────────────────

def test_a_legacy_scenario_draft_still_gets_the_five_headings():
    """A draft written before the v4 shape has no what_went_wrong node at all.
    Rendering nothing for it would make an old RCA look like a broken
    composer, which is the failure mode this codebase is built around."""
    text = compose_legacy(
        [{"scenario_name": "Late entry", "accuracy": "Accurate",
          "why": "queue mismanaged", "fix": "add a queue SOP"}], None)
    for h in headings():
        assert h in text
    assert "queue mismanaged" in text
    assert "add a queue SOP" in text


def test_a_legacy_chain_draft_also_gets_the_five_headings():
    text = compose_legacy(None, [{"step": 1, "what": "Ticket not sent",
                                  "why": "vendor API down"}])
    for h in headings():
        assert h in text
    assert "vendor API down" in text


def test_legacy_with_nothing_in_it_composes_to_nothing():
    assert compose_legacy(None, None) == ""
    assert compose_legacy([], []) == ""


# ── NEGATIVE source assertions: the client has no second composer ───────────
#
# CLAUDE.md §2 allows source assertions only for NEGATIVE claims and for
# client-side JavaScript, which has no test harness here. Both apply: these
# assert that a string appears NOWHERE in the client, which unreachable code
# cannot defeat, and the subject is client JS.

def test_the_client_does_not_compose_the_wwr_section_itself():
    """The client used to build this section from rca.v3 in JavaScript while
    the server built it in Python. Two composers for one block of text is how
    "Fix: [object Object]" reached a real post from the client half while the
    server half was correct — a defect no server-side test could ever see."""
    assert "wwrParts" not in CLIENT
    assert "'• Fix: '" not in CLIENT
    assert '"• Fix: "' not in CLIENT


def test_the_client_does_not_read_the_wwr_fields_to_build_a_post():
    """The section's ingredients are only touched by the server composer now.
    `claim_accuracy_note` and `sop_gap` still render on the CARD, so this
    checks the concatenations the composer used, not the field names."""
    assert "'• Why that verdict: '" not in CLIENT
    assert "'• Root cause: '" not in CLIENT
    assert "'• Operational failure: '" not in CLIENT


def test_the_client_renders_the_servers_text():
    """The positive half is behavioural and lives in
    test_wwr_one_composer.py::test_the_draft_carries_the_same_text_the_post_does.
    This only pins that the client reads the server's field at all — a
    negative assertion cannot say that."""
    assert "rca.wwrSlackText" in CLIENT
    assert "draft.wwr_slack_text" in CLIENT
