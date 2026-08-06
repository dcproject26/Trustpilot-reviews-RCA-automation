"""Who picks up the fix.

THE MIS-ROUTE THIS EXISTS TO STOP. `actions_from_findings` fell back on
`_sole` — the single team holding a flag anywhere on the card — whenever a
fix named no owner. One CONTENT flag and a refund fix put the refund on
CONTENT's tab. The module's own docstring calls that worse than a row
reported as unplaced, and it was doing it anyway.
"""
from server.checklist import actions_from_findings, team_for_fix


def _issue(**kw):
    kw.setdefault("issue", "Refund never arrived")
    return kw


# ── team_for_fix, the three bases ──────────────────────────────────────────

def test_a_stated_owner_is_used_and_said_to_be_stated():
    team, how = team_for_fix(
        _issue(fix={"owner": "FINANCE", "action": "chase the refund"}), [])
    assert team == "finance"
    assert "names its owner" in how


def test_an_owner_less_fix_ties_to_a_flag_on_the_SAME_issue():
    team, how = team_for_fix(
        _issue(operational_failure="the refund was never raised with the bank",
               fix={"action": "raise it"}),
        [{"team": "FINANCE", "flag": "refund was never raised with the bank"}])
    assert team == "finance", (team, how)
    assert "matched to a flag" in how


def test_an_unrelated_flag_does_not_claim_the_fix():
    """The whole defect: one flag on the card is not evidence about a fix."""
    team, how = team_for_fix(
        _issue(operational_failure="the refund was never raised with the bank",
               fix={"action": "raise it"}),
        [{"team": "CONTENT", "flag": "variant missing on the experience page"}])
    assert team == "", (team, how)
    assert "no flag on this issue matches" in how


def test_an_unroutable_fix_is_reported_not_parked():
    """A row silently routed to the wrong team is worse than one a reader is
    told went nowhere."""
    tabs, rep = actions_from_findings(
        [_issue(operational_failure="the refund was never raised",
                fix={"action": "raise the refund with the bank"})],
        [{"team": "CONTENT", "flag": "variant missing on the experience page"}])
    assert tabs["content"] == ["variant missing on the experience page"], tabs
    assert not any("refund" in r for r in tabs["finance"]), tabs["finance"]
    assert rep["unrouted"], "an unplaceable fix must be reported"
    assert any("could not be routed" in n for n in rep["notes"]), rep["notes"]


def test_the_stated_owner_wins_over_the_only_flagged_team():
    tabs, _ = actions_from_findings(
        [_issue(fix={"owner": "FINANCE", "action": "chase the refund"})],
        [{"team": "CONTENT", "flag": "variant missing on the page"}])
    assert "chase the refund" in tabs["finance"], tabs
    assert "chase the refund" not in tabs["content"], tabs


def test_routing_by_flag_match_is_announced_as_a_judgement():
    """Grouping by a match is a guess; the trail says one was made."""
    _, rep = actions_from_findings(
        [_issue(operational_failure="the refund was never raised with the bank",
                fix={"action": "raise it with the bank"})],
        [{"team": "FINANCE", "flag": "refund was never raised with the bank"}])
    assert any("a judgement, not a stated owner" in n for n in rep["notes"]), \
        rep["notes"]


def test_a_flag_still_routes_on_its_own_team():
    tabs, _ = actions_from_findings([], [{"team": "TECH", "flag": "BMS timed out"}])
    assert tabs["tech"] == ["BMS timed out"], tabs


def test_nothing_found_says_so_rather_than_rendering_empty():
    _, rep = actions_from_findings([], [])
    assert any("nothing this case found to raise" in n for n in rep["notes"]), \
        rep["notes"]
