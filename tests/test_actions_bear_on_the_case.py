"""A guideline row must also bear on what THIS case found.

THE REPORT. A booking reassigned to a new operator without the guest's consent,
remedied with a partial wallet credit. The Supply Partner tab showed:

    Verify meeting point with SP if reported
    BMS refund error → raise with Leads on #co-issue or Fin on priority
    Share ARN number for delayed refunds

No meeting point in the case. No BMS refund error. No delayed refund needing an
ARN. All three came from the routed scenario's guideline list and a flag naming
SP, so all three satisfied the AND — and all three were still wrong.

THE AND WAS NECESSARY, NOT SUFFICIENT. A guideline row for a scenario is not
automatically a step that was or should have been taken on this booking. The
third condition is subject-matter overlap with the card's own findings: the
root cause, the operational failure, the SOP gap, the fix, and the flags.

DELIBERATELY CRUDE. A loose match leaves a row on the card, which is the safe
direction; a tight one silently empties a tab, and an empty tab that means
"we filtered everything" is indistinguishable from one that means "nothing was
flagged" unless it is reported. It is reported.
"""
import pytest

from server.checklist import (ACTION_TABS, actions_raised, findings_text,
                              _relevance_tokens)

# The screenshot's rows, verbatim.
SP_ROWS = ["Verify meeting point with SP if reported",
           "BMS refund error → raise with Leads on #co-issue or Fin on priority",
           "Share ARN number for delayed refunds"]

REASSIGNMENT = (
    "The booking was reassigned to a different operator without the guest's "
    "consent. Nobody notified the guest that the operator had changed. "
    "Remedied with a partial wallet credit.")


def _raise(rows, findings, team="sp", flag="The SP reassigned the booking"):
    """Drive actions_raised with a stubbed guideline list for one team."""
    import server.checklist as C
    real = C.actions_for
    C.actions_for = lambda names: {t: (list(rows) if t == team else [])
                                   for t in ACTION_TABS}
    try:
        return actions_raised(["Tickets sent late"],
                              [{"team": team.upper(), "flag": flag,
                                "evidence": ""}],
                              findings=findings)
    finally:
        C.actions_for = real


def test_the_stub_actually_produces_rows_without_the_filter():
    """NOT BUILT guard. If the stub raised nothing, every case below would be
    checking an empty list — which is the failure this suite punishes."""
    tabs, report = _raise(SP_ROWS, None)
    assert tabs["sp"] == SP_ROWS, tabs
    assert report["relevance_checked"] is False


def test_the_three_irrelevant_rows_are_withheld():
    """The reported case, end to end."""
    tabs, report = _raise(SP_ROWS, REASSIGNMENT)
    assert tabs["sp"] == [], (
        f"a row with nothing to do with this case is still on the card: "
        f"{tabs['sp']}")
    assert report["irrelevant"] == 3, report
    assert report["relevance_checked"] is True


def test_what_was_withheld_is_named_not_counted_silently():
    _, report = _raise(SP_ROWS, REASSIGNMENT)
    said = [n for n in report["notes"] if "not bearing on this case" in n]
    assert said, f"three rows vanished and nothing said so: {report['notes']}"
    assert "3 guideline action(s) withheld" in said[0], said[0]
    assert "meeting point" in said[0], (
        f"the withheld rows are counted but not named: {said[0]}")
    assert "subject-matter overlap" in said[0], (
        "the note does not say a judgement was made, or on what basis")


def test_a_row_that_does_bear_on_the_case_survives():
    """The other direction. A filter that drops everything is not a filter."""
    rows = SP_ROWS + ["Raise the unconsented operator reassignment with the SP"]
    tabs, report = _raise(rows, REASSIGNMENT)
    assert tabs["sp"] == ["Raise the unconsented operator reassignment with the SP"], tabs
    assert report["irrelevant"] == 3
    assert report["raised"] == 1


def test_the_flag_can_carry_a_row_the_prose_does_not():
    """The flag IS a finding, and its wording is often closer to the guideline
    row than the prose of the root cause. A row matching the flag stays."""
    tabs, _ = _raise(["Share ARN number for delayed refunds"],
                     "The guest was not notified of the operator change.",
                     flag="The refund ARN was never shared with the guest")
    assert tabs["sp"] == ["Share ARN number for delayed refunds"], (
        "a row the flag plainly describes was withheld")


def test_a_card_with_no_findings_withholds_nothing():
    """Nothing was found, so nothing can be SHOWN to bear on it. Withholding
    everything would be the filter deciding a question it has no evidence for,
    and would empty every tab on a card whose RCA failed."""
    tabs, report = _raise(SP_ROWS, "")
    assert tabs["sp"] == SP_ROWS, tabs
    assert report["irrelevant"] == 0


def test_omitting_findings_is_reported_as_not_checked():
    """A filter that never ran and a filter that ran and withheld nothing are
    the two things this codebase must never let look alike."""
    _, report = _raise(SP_ROWS, None)
    assert report["relevance_checked"] is False
    assert any("was NOT checked" in n for n in report["notes"]), report["notes"]
    assert report["irrelevant"] == 0


def test_the_three_empties_are_three_different_sentences():
    """Nothing flagged, no guideline row, and nothing that bears on the case
    are the same blank tab and mean different things."""
    import server.checklist as C
    real = C.actions_for

    def _notes(rows, flags, findings):
        C.actions_for = lambda names: {t: (list(rows) if t == "sp" else [])
                                       for t in ACTION_TABS}
        try:
            return actions_raised(["Tickets sent late"], flags,
                                  findings=findings)[1]["notes"]
        finally:
            C.actions_for = real

    unflagged = _notes(SP_ROWS, [], REASSIGNMENT)
    no_rows = _notes([], [{"team": "SP", "flag": "x", "evidence": ""}],
                     REASSIGNMENT)
    irrelevant = _notes(SP_ROWS, [{"team": "SP", "flag": "x", "evidence": ""}],
                        REASSIGNMENT)

    assert any("nothing was flagged" in n for n in unflagged), unflagged
    assert any("no guideline action" in n for n in no_rows), no_rows
    assert any("not bearing on this case" in n for n in irrelevant), irrelevant
    sets = [{n for n in ns if "actions taken" in n}
            for ns in (unflagged, no_rows, irrelevant)]
    assert len({frozenset(x) for x in sets}) == 3, (
        f"two of the three empty states produce the same sentence: {sets}")


# ── the tokeniser, because the whole rule rests on it ───────────────────────

def test_a_shared_stopword_is_not_a_match():
    assert not (_relevance_tokens("Raise with the team if reported")
                & _relevance_tokens("Check with the team when it is raised"))


def test_a_plural_matches_its_singular():
    assert _relevance_tokens("delayed refunds") & _relevance_tokens("refund was late")


def test_findings_text_reads_every_field_a_row_could_bear_on():
    txt = findings_text({
        "what_went_wrong": {"guest_issues": [{
            "issue": "Reassigned",
            "root_cause": "ROOTCAUSEWORD",
            "operational_failure": "OPFAILWORD",
            "sop_gap": "SOPGAPWORD",
            "fix": {"action": "FIXACTIONWORD", "because": "FIXBECAUSEWORD"}}]},
        "flags": [{"flag": "FLAGWORD", "evidence": "EVIDENCEWORD"}]})
    for w in ("ROOTCAUSEWORD", "OPFAILWORD", "SOPGAPWORD", "FIXACTIONWORD",
              "FIXBECAUSEWORD", "FLAGWORD", "EVIDENCEWORD"):
        assert w in txt, f"{w} is not in the findings the filter reads"


def test_findings_text_of_an_empty_card_is_empty():
    assert findings_text({}) == ""
    assert findings_text(None) == ""


def test_it_runs_on_the_real_path():
    """The wire. A condition validate() does not apply is a condition that
    does not exist — this project has shipped exactly that before."""
    from server.services.rca_v4_validate import validate
    rca = {"l1": "Operations Issue", "l2": "Ticket Issues",
           "sub_themes": ["C. Ticket Delayed"], "stated_issue": "x",
           "what_went_wrong": {"guest_issues": [{
               "issue": "Operator changed without consent",
               "claim": "They swapped my operator.",
               "claim_accuracy": "Accurate",
               "claim_accuracy_note": "The log shows it.",
               "root_cause": "The reassignment sent no notification.",
               "operational_failure": "Nobody watched operator changes.",
               "evidence": []}]},
           "flags": [{"team": "SP", "flag": "Operator reassigned without consent",
                      "evidence": "log"}],
           "takedown": {"verdict": "No"}, "area_of_improving": [],
           "booking_logs": [], "resolution": "Wallet credit.",
           "suggested_response": "Sorry."}
    _, notes = validate(rca, ["Tickets sent late"])
    assert not any("was NOT checked" in n for n in notes), (
        f"validate() is not passing findings, so the third condition never "
        f"runs on the real path: {notes}")
