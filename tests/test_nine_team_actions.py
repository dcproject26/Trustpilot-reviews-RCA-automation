"""Actions Taken is computed where both halves of the rule are in hand.

HANDOFF §1. A row appears because the DSS guidelines say it must be raised AND
because it has been flagged. The guidelines come from the routed scenarios and
the flags come from the RCA, and the one place that holds both is `validate` —
which is why it computes the section rather than copying one the model wrote.

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
    assert set(out["actions_taken"]) == set(ACTION_TEAMS)


def test_a_flagged_team_gets_the_guideline_step_for_the_routed_scenario():
    out, _ = validate(_rca(), [SCENARIO])
    assert out["actions_taken"]["co"] == actions_for([SCENARIO])["co"]
    assert out["actions_taken"]["co"], "the fixture prescribes nothing for CO"


def test_an_unflagged_team_gets_nothing_however_much_is_prescribed():
    out, _ = validate(_rca(), [SCENARIO])
    prescribed = actions_for([SCENARIO])
    withheld = [t for t, v in prescribed.items() if v and t != "co"]
    assert withheld, "the fixture prescribes nothing to withhold"
    for team in withheld:
        assert out["actions_taken"][team] == [], team


def test_what_was_withheld_is_reported_to_the_caller():
    """The pipeline writes every note `validate` returns onto the confidence
    trail as a warn. A withheld row that is never mentioned is a row the
    reader cannot tell from one that was never prescribed."""
    _, notes = validate(_rca(), [SCENARIO])
    said = " ".join(n for n in notes if "actions taken" in n)
    assert "withheld" in said, notes


def test_nothing_flagged_says_so_rather_than_reporting_a_bare_zero():
    _, notes = validate(_rca(flags=[]), [SCENARIO])
    assert any("nothing was flagged" in n for n in notes), notes


def test_a_clean_run_puts_nothing_on_the_trail():
    """Everything prescribed was raised: the rows are the report. A note on
    every healthy run is how a trail stops being read.

    Each flag carries the wording of its own team's guideline rows, so the
    third condition — does this row bear on what the case found — passes for
    all of them. A flag reading "x" would fail it, and rightly: a row about
    escalating a recurring issue has nothing to do with a card that never
    mentions one.
    """
    guideline = actions_for([SCENARIO])
    flags = [{"team": t.upper(), "flag": " ".join(v), "evidence": "y"}
             for t, v in guideline.items() if v]
    _, notes = validate(_rca(flags=flags), [SCENARIO])
    assert not [n for n in notes if "actions taken" in n], notes


# ── the projection: the value has to reach the column ───────────────────────

def test_actions_taken_is_one_of_the_projected_columns():
    assert V4_PROJECTION["actions_taken"] == ("actions_taken",)


def test_the_computed_section_survives_the_projection():
    """Driven through project_v4 — the same function the pipeline and
    regenerate-rca both call — rather than asserting that a line assigning it
    appears in pipeline.py, which passes just as happily when that line is
    unreachable."""
    out, _ = validate(_rca(), [SCENARIO])
    col = project_v4(out)["actions_taken"]
    assert col == out["actions_taken"]
    assert col["co"], "the section reached the column empty"


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
