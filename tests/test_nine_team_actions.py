"""Actions Taken is computed from what THIS CASE FOUND.

It used to be sourced from the DSS guideline sheet for the routed scenario and
then filtered — by flagged team, and by word overlap with the findings. Two
filters applied to the wrong source. Guideline rows are a PLAYBOOK for a
scenario, not things that happened on this booking, so "Share ARN number for
delayed refunds" reached a card with no delayed refund: a valid row for the
scenario, passing both filters, and still a statement about work nobody did.
The section is read as "this is what we did", which no playbook row can
honestly say.

ONE SOURCE NOW, NOT SIX. It was the flags, each issue's operational failure,
its SOP gap, its fix, the improvement points and the DSS miss — all merged
into a store of its own. That is how one remediation reached a card twice:
as the fix that closes a gap, and again as the flag that raised it, worded
differently enough to survive the repeat check.

Actions Taken is now a VIEW over §3's `fixes`, grouped by owner. The other
five each render in their own section already — a flag is the hand-off and
says which team must act, so routing it here as well said it twice. A flag
with no fix therefore puts nothing on a tab, and that is the intended
reading: the Flags section is where a team is handed work.

UNROUTED IS A TAB. A fix naming no team used to be reported in `notes` under
a tab strip that looked complete, which is the shape of a finished card with
a row nobody will pick up.

`validate` still computes it, because that is the one place holding the
validated issues, the validated flags and the improvement points at once.

Two things this file guards that a routing test cannot:

  * the computed value REACHES THE COLUMN the dashboard reads. `validate`
    returning the right dict proves nothing about the projection carrying it,
    and a projection that quietly dropped it would empty the section on every
    card while every routing test stayed green;
  * the run SAYS what it withheld. A tab empty because nothing was flagged and
    a tab empty because the guidelines prescribe nothing are the same blank
    space and opposite facts. The notes `validate` returns are what the
    pipeline puts on the confidence trail.
"""
import pytest

from server.checklist import ACTION_TEAMS, actions_for
from server.services.rca_v4_validate import V4_PROJECTION, project_v4, validate

SCENARIO = "Tickets sent late"


def _rca(**over):
    base = {
        "l1": "Operations Issue", "l2": "Ticket Issues",
        "what_went_wrong": {"guest_issues": [{
            "issue": "Tickets arrived after the slot",
            "claim": "They came two hours late.",
            "claim_accuracy": "Accurate",
            "root_cause": "The fulfilment run failed silently.",
            "operational_failure": "Nobody watched the fulfilment queue",
            "evidence": []}]},
        "flags": [{"team": "CO", "flag": "No follow-up after the first reply",
                   "evidence": "ZD-1"}],
        "takedown": {"verdict": "No"},
    }
    base.update(over)
    return base


def test_the_section_is_computed_not_copied():
    """A model-supplied actions_taken would be the model deciding what we did,
    which is the one thing this section must never be."""
    out, _ = validate(_rca(actions_taken={"sp": ["We definitely did this"]}),
                      [SCENARIO])
    flat = [a for v in out["actions_taken"].values() for a in v]
    assert "We definitely did this" not in flat


def test_every_tab_key_is_present_even_when_empty():
    """An absent key and an empty list render the same and mean different
    things: one is a tab with nothing raised, the other is a tab the
    projection forgot."""
    out, _ = validate(_rca(), [SCENARIO])
    from server.checklist import ACTION_TAB_ORDER
    assert set(out["actions_taken"]) == set(ACTION_TAB_ORDER)


def test_a_fix_lands_on_its_owners_tab_and_no_playbook_row_lands_anywhere():
    """The section is the fixes, grouped by owner."""
    rca = _rca()
    rca["what_went_wrong"]["fixes"] = [
        {"action": "Alert on failed fulfilment", "owner": "TECH"}]
    out, _ = validate(rca, [SCENARIO])
    assert "Alert on failed fulfilment" in out["actions_taken"]["tech"]


def test_a_flag_with_no_fix_reaches_no_tab():
    """It used to. A flag already names the team that must act and renders in
    the Flags section; putting it here as well said it twice, which is the
    repetition this restructure exists to remove."""
    out, _ = validate(_rca(), [SCENARIO])          # fixture has a CO flag
    assert out["actions_taken"]["co"] == [], out["actions_taken"]["co"]
    assert out["flags"][0]["flag"] == "No follow-up after the first reply", \
        "the flag itself must still render — it moved, it did not vanish"


def test_no_guideline_row_reaches_the_card():
    """"Share ARN number for delayed refunds" on a card with no delayed
    refund is the reported bug. The sheet's rows are not a source any more,
    so NONE of them may appear — not the ones this scenario routes, and not
    the ones any other scenario routes."""
    out, _ = validate(_rca(), [SCENARIO])
    raised = {a for v in out["actions_taken"].values() for a in v}
    from server.checklist import _ALL_GUIDELINE_ACTIONS
    assert not (raised & _ALL_GUIDELINE_ACTIONS), raised & _ALL_GUIDELINE_ACTIONS


def test_a_team_with_no_finding_gets_nothing_however_much_is_prescribed():
    out, _ = validate(_rca(), [SCENARIO])
    prescribed = actions_for([SCENARIO])
    assert any(prescribed.values()), "the fixture prescribes nothing at all"
    for team in (t for t in prescribed if t != "co"):
        assert out["actions_taken"][team] == [], team


def test_a_finding_that_names_no_team_is_reported_rather_than_parked():
    """CLAUDE.md §1. A finding that could not be routed is NOT on the card,
    and the reader must not have to infer that from a tab that looks
    complete. It is also not dropped onto a plausible-looking tab, which
    would be worse: a row attributed to a team that did nothing wrong."""
    rca = _rca(flags=[{"team": "CO", "flag": "a", "evidence": "e"},
                      {"team": "SP", "flag": "b", "evidence": "e"}])
    rca["what_went_wrong"]["guest_issues"][0]["operational_failure"] = \
        "Nobody watched the fulfilment queue"
    rca["what_went_wrong"]["fixes"] = [{"action": "Someone should look at this"}]
    out, notes = validate(rca, [SCENARIO])
    assert "Someone should look at this" in out["actions_taken"]["unrouted"], \
        "an unowned fix belongs on a tab a reader can see, not in a footnote"
    said = " ".join(n for n in notes if "actions taken" in n)
    assert "Unrouted tab" in said, notes


def test_nothing_found_says_so_rather_than_reporting_a_bare_zero():
    """An empty section because nothing was found, and an empty section
    because the builder broke, are the same blank space on screen."""
    _, notes = validate(_rca(flags=[], what_went_wrong={"guest_issues": []}),
                        [SCENARIO])
    said = " ".join(n for n in notes if "actions taken" in n)
    assert "no fix" in said, notes


def test_the_same_finding_worded_twice_is_not_two_rows():
    """A root cause, a flag and an improvement point routinely say one thing
    three ways. Printing all three reads as three pieces of work — and the
    merge is a JUDGEMENT, so the run says it made one."""
    rca = _rca()
    rca["what_went_wrong"]["fixes"] = [
        {"action": "Watch the fulfilment queue", "owner": "CO"},
        {"action": "Watch the fulfilment queue", "owner": "CO"}]
    out, notes = validate(rca, [SCENARIO])
    assert len(out["actions_taken"]["co"]) == 1, out["actions_taken"]["co"]
    assert any("already said" in n for n in notes), notes


def test_dss_contributes_no_rows_of_its_own():
    """It used to supply the missed escalation step directly. A step DSS says
    should have happened is a FIX — write it as one, with an owner — rather
    than a seventh source feeding the same tabs by another route. What the
    sheet prescribes has never belonged here."""
    rca = _rca()
    rca["dss"] = {"prescribes": "Refund within 5 days", "ref": "R-1",
                  "missed_next_step": [
                      {"team": "CO",
                       "action": "Escalate to leads after 24h with no reply"}]}
    out, _ = validate(rca, [SCENARIO])
    raised = {a for v in out["actions_taken"].values() for a in v}
    assert "Refund within 5 days" not in raised, raised
    assert "Escalate to leads after 24h with no reply" not in raised, raised


def test_a_clean_run_puts_nothing_on_the_trail():
    """Every finding routed and nothing merged: the rows are the report. A
    note on every healthy run is how a trail stops being read."""
    rca = _rca(flags=[{"team": "CO", "flag": "No follow-up after the first "
                                             "reply", "evidence": "ZD-1"}])
    rca["what_went_wrong"]["guest_issues"][0].pop("operational_failure", None)
    rca["what_went_wrong"]["fixes"] = [
        {"action": "Follow up within the promised window", "owner": "CO"}]
    _, notes = validate(rca, [SCENARIO])
    assert not [n for n in notes if "actions taken" in n], notes


# ── the projection: the value has to reach the column ───────────────────────

def test_actions_taken_is_one_of_the_projected_columns():
    assert V4_PROJECTION["actions_taken"] == ("actions_taken",)


def test_the_computed_section_survives_the_projection():
    """Driven through project_v4 — the same function the pipeline and
    regenerate-rca both call — rather than asserting that a line assigning it
    appears in pipeline.py, which passes just as happily when that line is
    unreachable."""
    rca = _rca()
    rca["what_went_wrong"]["fixes"] = [
        {"action": "Alert on failed fulfilment", "owner": "TECH"}]
    out, _ = validate(rca, [SCENARIO])
    col = project_v4(out)["actions_taken"]
    assert col == out["actions_taken"]
    assert col["tech"], "the section reached the column empty"


def test_a_failed_rca_projects_an_empty_dict_not_a_missing_column():
    """No RCA means no flags, which means nothing was raised. That is the
    honest answer, and it must be a dict the renderer can iterate rather than
    a None it cannot."""
    assert project_v4({})["actions_taken"] == {}
    assert project_v4(None)["actions_taken"] == {}


def test_the_teams_the_flags_use_are_the_teams_the_tabs_use():
    """One vocabulary. Two spellings of a team would make the join match
    nothing, which on screen is indistinguishable from a card with nothing to
    raise."""
    from server.services.rca_v4_validate import FLAG_TEAMS
    assert set(FLAG_TEAMS) == {t.upper() for t in ACTION_TEAMS} | {"OTHER"}
