"""DSS is what a gap is CHECKED against, not what the reader is shown.

Two things reached a card and neither should have:

  1. An EVIDENCE ROW sourced `dss` — `dss — "DSS matched row is for 'Tour
     started late / guide arrived late at MP'; no row covers a
     system-initiated vendor reassignment."` That is a remark about our own
     decision sheet's coverage sitting in a list of records of what happened
     to this booking.

  2. `sop_gap` and `fix` written as DSS paths — "No DSS path governs a
     system-initiated vendor reassignment…" and "Define a DSS path for
     system-initiated vendor reassignments…". The reader of those fields owns
     an operation, not a spreadsheet.

THE TENSION, STATED RATHER THAN BURIED. Prompt rule 2f says `sop_gap` comes
from DSS, and that rule came from the written what_went_wrong spec. This does
not delete it — the DSS lookup is still how you know whether a control existed
and whether it was followed. It NARROWS it: the lookup informs the answer, and
the answer is written about the process. "No row covers this" stops being the
gap; "nobody was required to contact the guest" becomes it.

THE REMOVAL IS NARROW ON PURPOSE. `dss` stays in SOURCES because `fix.source`
(where a gap was READ — a field that never renders) and issue_specific_answers
both still use it. Only the evidence path is closed.
"""
import pytest

from server.services.rca_v4_validate import (EVIDENCE_SOURCES, SOURCES,
                                             _evidence_rows, _fix_obj, validate)


def _ok(**kw):
    """A minimal RCA that passes validation, with one guest issue."""
    issue = {"issue": "Vendor reassigned the slot",
             "claim": "They moved my tour and never told me.",
             "claim_accuracy": "Accurate",
             "claim_accuracy_note": "The booking log shows the reassignment.",
             "root_cause": "The vendor reassignment did not notify the guest.",
             "operational_failure": "No notification fired on reassignment.",
             "sop_gap": None,
             "fix": {"action": "Notify the guest on reassignment",
                     "owner": "CO", "because": "The window closed unnoticed",
                     "source": "booking"},
             "evidence": []}
    issue.update(kw.pop("issue_patch", {}))
    base = {"l1": "Operations Issue", "l2": "Ticket Issues",
            "sub_themes": ["C. Ticket Delayed"],
            "stated_issue": "Slot moved without notice.",
            "what_went_wrong": {"guest_issues": [issue]},
            "flags": [], "booking_logs": [], "takedown": {"verdict": "No"},
            "area_of_improving": [], "resolution": "Refunded.",
            "suggested_response": "We are sorry.",
            "dss": {"prescribes": "Notify on reassignment.", "ref": None}}
    base.update(kw)
    return base


# ── the enum itself ─────────────────────────────────────────────────────────

def test_the_two_enums_were_imported_and_differ():
    """NOT BUILT guard. If these were the same object every case below would
    be checking nothing."""
    assert "dss" in SOURCES, (
        "dss was removed from SOURCES globally — fix.source and "
        "issue_specific_answers still use it, so the removal was meant to be "
        "narrowed to the evidence path")
    assert "dss" not in EVIDENCE_SOURCES
    assert set(SOURCES) - set(EVIDENCE_SOURCES) == {"dss"}, (
        "the evidence enum differs from SOURCES by more than dss")


# ── (1) evidence rows ───────────────────────────────────────────────────────

def test_an_evidence_row_sourced_dss_loses_its_source():
    notes = []
    rows = _evidence_rows([{
        "text": "DSS matched row is for 'Tour started late'; no row covers a "
                "system-initiated vendor reassignment.",
        "source": "dss", "ref": None, "backs_claim": None}], notes)
    assert len(rows) == 1, "the row was dropped rather than demoted"
    assert rows[0]["source"] is None, (
        f"the DSS source survived: {rows[0]['source']!r}")


def test_the_text_of_a_demoted_row_is_kept():
    """Dropping the row would delete a sentence the model wrote. The finding
    may still be worth reading; it is the SOURCE that was wrong."""
    notes = []
    rows = _evidence_rows([{"text": "No row covers this reassignment.",
                            "source": "dss"}], notes)
    assert rows[0]["text"] == "No row covers this reassignment."


def test_the_demotion_is_counted_and_said():
    """A source that quietly became null looks exactly like one the model
    never supplied."""
    notes = []
    _evidence_rows([{"text": "a", "source": "dss"},
                    {"text": "b", "source": "dss"},
                    {"text": "c", "source": "zendesk"}], notes)
    said = [n for n in notes if "DSS" in n]
    assert len(said) == 1, f"the demotion was not reported once: {notes}"
    assert "2 evidence row(s)" in said[0], said[0]


def test_a_clean_evidence_list_says_nothing():
    """The inverse bug. A note on every healthy run makes a healthy run look
    faulty and teaches the reader to skip the trail."""
    notes = []
    _evidence_rows([{"text": "The booking log shows the reassignment.",
                     "source": "booking"}], notes)
    assert notes == [], f"a clean list produced notes: {notes}"


@pytest.mark.parametrize("src", [s for s in EVIDENCE_SOURCES])
def test_every_other_source_still_passes(src):
    """The removal must not have taken a neighbour with it."""
    notes = []
    rows = _evidence_rows([{"text": "x", "source": src}], notes)
    assert rows[0]["source"] == src, (
        f"{src!r} stopped being a valid evidence source")
    assert notes == []


def test_a_legacy_bracket_prefix_is_still_stripped():
    """`[dss] the finding` is the old string shape. Leaving it unmatched would
    render the bracket inline, which is the defect the structured fields exist
    to remove — so it is still RECOGNISED, and only the source is dropped."""
    notes = []
    rows = _evidence_rows(["[dss] No row covers this reassignment."], notes)
    assert rows[0]["text"] == "No row covers this reassignment.", (
        f"the prefix is still in the sentence: {rows[0]['text']!r}")
    assert rows[0]["source"] is None
    assert any("DSS" in n for n in notes), notes


def test_a_legacy_prefix_for_a_live_source_still_lifts():
    notes = []
    rows = _evidence_rows(["[zendesk] CE told the guest two hours."], notes)
    assert rows[0]["source"] == "zendesk"
    assert rows[0]["text"] == "CE told the guest two hours."


def test_it_reaches_the_card_through_validate():
    """The wire, not the checker. A demotion that never runs on the real path
    is the shape of bug this project keeps finding."""
    out, notes = validate(_ok(issue_patch={"evidence": [
        {"text": "DSS has no row for this.", "source": "dss"},
        {"text": "The log shows the reassignment.", "source": "booking"}]}))
    ev = out["what_went_wrong"]["guest_issues"][0]["evidence"]
    assert [e["source"] for e in ev] == [None, "booking"], ev
    assert any("DSS" in n for n in notes), notes


# ── the narrowing: dss survives where it was not the complaint ──────────────

def test_fix_source_may_still_be_dss():
    """It records where a gap was READ, and it never renders. DSS is still
    what you check a gap against — that half of rule 2f is not deleted."""
    notes = []
    fx = _fix_obj({"action": "Notify the guest", "owner": "CO",
                   "because": "The window closed unnoticed",
                   "source": "dss"}, notes)
    assert fx["source"] == "dss", (
        "fix.source lost dss — the removal was meant to be narrowed to the "
        "evidence path, and this field is where the lookup legitimately lives")


def test_issue_specific_answers_may_still_cite_dss():
    """Outside what_went_wrong entirely, and the section is off the card."""
    out, _ = validate(_ok(issue_specific_answers=[
        {"question": "Was a refund due?", "verdict": "Yes",
         "evidence": "The playbook prescribes one.", "source": "dss",
         "ref": None}]))
    isa = out.get("issue_specific_answers") or []
    assert isa and isa[0]["source"] == "dss", isa


# ── (2) the gap and the fix written as DSS coverage ─────────────────────────

DSS_GAP = ("No DSS path governs a system-initiated vendor reassignment that "
           "compresses or eliminates the guest's rescheduling window.")
DSS_FIX = "Define a DSS path for system-initiated vendor reassignments."


def test_a_gap_written_as_sheet_coverage_is_reported():
    _, notes = validate(_ok(issue_patch={"sop_gap": DSS_GAP}))
    said = [n for n in notes if "sop_gap" in n and "DSS" in n]
    assert said, f"the DSS-shaped gap went unreported: {notes}"
    assert "not something the finding talks about" in said[0], said[0]


def test_a_fix_written_as_authoring_a_dss_row_is_reported():
    _, notes = validate(_ok(issue_patch={
        "fix": {"action": DSS_FIX, "owner": "CO",
                "because": "No row covers it", "source": "booking"}}))
    assert any("fix.action" in n for n in notes), notes
    assert any("fix.because" in n for n in notes), (
        f"'No row covers it' in fix.because went unreported: {notes}")


def test_the_sentence_is_kept_not_rewritten():
    """There is no mechanical way to restate an analysis correctly. Deleting
    it loses a real finding; paraphrasing puts words in the model's mouth."""
    out, _ = validate(_ok(issue_patch={"sop_gap": DSS_GAP}))
    assert out["what_went_wrong"]["guest_issues"][0]["sop_gap"] == DSS_GAP


def test_a_process_worded_gap_and_fix_are_silent():
    """The whole point. If this fired here too, the note would mean nothing
    and every clean run would carry a warning."""
    _, notes = validate(_ok(issue_patch={
        "sop_gap": "Nobody was required to contact the guest when the "
                   "reassignment compressed the rescheduling window.",
        "fix": {"action": "Require proactive notification whenever a "
                          "reassignment shortens the rescheduling window",
                "owner": "CO",
                "because": "The window closed with no one accountable",
                "source": "booking"}}))
    assert not [n for n in notes if "DSS sheet's coverage" in n], notes


# ── the prompt no longer offers dss as an evidence source ───────────────────
#
# NEGATIVE source assertions, which CLAUDE.md permits: unreachability cannot
# defeat "this string appears nowhere". The prompt is a data file with no
# behaviour of its own to drive.

def test_the_evidence_schema_does_not_offer_dss():
    from server.prompts import RCA_V4_TEMPLATE as BODY
    assert '"source": "<booking | bms | zendesk | insights | dss | exp-page>"' \
        not in BODY, "the evidence schema still lists dss as a source"
    # fix.source keeps it, and is spelled with its own alignment. Asserted
    # POSITIVELY so the narrowing is visible here rather than inferred from
    # the absence above.
    assert '"source":   "<booking | bms | zendesk | insights | dss | exp-page>"' \
        in BODY, ("fix.source lost dss from the schema — the removal was meant "
                  "to be narrowed to the evidence path")


def test_the_prompt_still_tells_the_model_to_look_dss_up():
    """The other half. Removing the source must not remove the LOOKUP — that
    is how the model knows whether a control existed at all."""
    from server.prompts import RCA_V4_TEMPLATE as BODY
    assert "Look up the needle for this" in BODY, (
        "the DSS lookup was removed along with the source — that is how the "
        "model works out what the next step should have been")
    assert "USE DSS TO DETERMINE WHAT THE NEXT STEP SHOULD HAVE BEEN" in BODY, (
        "rule 2f no longer frames DSS as a lookup that informs the answer")
    assert "REASON THE NEXT ESCALATION STEP" in BODY, (
        "a scenario with no DSS row no longer tells the model to reason the "
        "step from the playbook it does have — it will report the absence")


# ── DSS is named nowhere a reader sees a finding ────────────────────────────
#
# The instruction that superseded the first pass: DSS is a LOOKUP THAT INFORMS
# THE ANSWER, never a subject the answer talks about. The model consults it to
# work out what the next escalation step would have been and writes THAT step.

@pytest.mark.parametrize("field,patch", [
    ("root_cause", {"root_cause": "No DSS row covers a vendor reassignment."}),
    ("operational_failure",
     {"operational_failure": "The decision sheet has no path for this."}),
    ("sop_gap", {"sop_gap": DSS_GAP}),
])
def test_dss_named_in_any_finding_is_reported(field, patch):
    _, notes = validate(_ok(issue_patch=patch))
    assert any(field in n and "DSS" in n for n in notes), (
        f"{field} named the DSS and nothing said so: {notes}")


def test_dss_named_in_an_evidence_row_is_reported():
    """The text, not just the source. Dropping the `dss` source value does not
    stop the model writing the same remark into a row sourced `booking`."""
    _, notes = validate(_ok(issue_patch={"evidence": [
        {"text": "No DSS row covers a system-initiated reassignment.",
         "source": "booking"}]}))
    assert any("evidence[0]" in n for n in notes), (
        f"a DSS remark wearing a booking source went unreported: {notes}")


def test_a_case_that_names_dss_nowhere_is_silent():
    """The control. If this fired too, the note would mean nothing and every
    healthy run would carry a warning."""
    _, notes = validate(_ok(issue_patch={
        "root_cause": "The reassignment did not trigger a guest notification.",
        "operational_failure": "No notification fires on operator change.",
        "sop_gap": "Nobody was required to contact the guest before the "
                   "rescheduling window closed.",
        "evidence": [{"text": "The booking log shows the reassignment.",
                      "source": "booking"}]}))
    assert not [n for n in notes if "DSS" in n], notes
