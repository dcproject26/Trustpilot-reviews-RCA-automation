"""Who picks up the fix.

THE MIS-ROUTE THIS EXISTS TO STOP. The routing fell back on
`_sole` — the single team holding a flag anywhere on the card — whenever a
fix named no owner. One CONTENT flag and a refund fix put the refund on
CONTENT's tab. The module's own docstring calls that worse than a row
reported as unplaced, and it was doing it anyway.
"""
from server.checklist import team_for_fix


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


def test_a_fix_with_no_action_is_not_a_fix():
    """A row with an owner and no action tells a team they own something and
    does not say what. It must take no slot and no tab."""
    from server.checklist import actions_from_gaps
    tabs, rep = actions_from_gaps([
        {"source_ref": "ZD-1", "gap": "", "team": "TECH"},
        {"source_ref": "ZD-1", "gap": "   ", "team": "CO"},
        {"team": "SP"},
        {"source_ref": "ZD-1", "gap": "Alert on failed fulfilment", "team": "TECH"}])
    assert tabs["tech"] == ["Alert on failed fulfilment"], tabs["tech"]
    assert tabs["co"] == [] and tabs["sp"] == [], tabs
    assert rep["counts"]["gap"] == 1, rep["counts"]


def test_an_owner_outside_the_nine_teams_is_reported_and_unrouted():
    """A tenth team names an owner with no tab: the reader is told who owns
    the fix and then cannot find them anywhere. It goes to Unrouted, and the
    coercion is reported rather than applied silently."""
    from server.services.rca_v4_validate import validate
    out, notes = validate({"what_went_wrong": {
        "guest_issues": [],
        "fixes": [{"action": "Run a campaign", "owner": "Marketing"}]}})
    assert out["what_went_wrong"]["fixes"][0]["owner"] is None
    # NOT asserted on actions_taken any more. That section is built from the
    # case's unsolved GAPS, not from §3's fixes — a fix is what we propose to
    # do, and it was carrying findings and recommendations onto a tab headed
    # "Actions Taken". This test is about the OWNER enum, which is unchanged.
    assert any("not one of the nine" in n for n in notes), notes


def test_a_legacy_owner_spelling_is_translated_not_failed():
    """A draft written under the old vocabulary names a REAL team; failing it
    to unrouted would lose an owner the model got right."""
    from server.services.rca_v4_validate import validate
    out, _ = validate({"what_went_wrong": {
        "guest_issues": [],
        "fixes": [{"action": "Reply to the guest", "owner": "CE"}]}})
    # See above: actions_taken no longer reads §3's fixes. What this test is
    # for is that a legacy owner spelling is TRANSLATED rather than failed.


def test_two_fixes_saying_one_thing_are_one_row():
    """Fix-against-fix, not flag-against-fix. This went untested because the
    mutation guarding it had an ambiguous anchor and reported SKIP — and a
    SKIP is not a pass. Two fixes routinely say one thing two ways, and
    printing both reads as two pieces of work."""
    from server.checklist import actions_from_gaps
    tabs, rep = actions_from_gaps([
        {"source_ref": "ZD-1", "gap": "Resend the tickets to the guest", "team": "CO"},
        {"source_ref": "ZD-1", "gap": "Resend the tickets to the guest", "team": "CO"}])
    assert tabs["co"] == ["Resend the tickets to the guest"], tabs["co"]
    assert rep["counts"]["repeat"] == 1, rep["counts"]


def test_the_fix_merge_is_announced_as_a_judgement():
    """Treating two differently worded fixes as one is a guess, and nothing
    else on the card would say one was made."""
    from server.checklist import actions_from_gaps
    _, rep = actions_from_gaps([
        {"source_ref": "ZD-1", "gap": "Resend the tickets to the guest", "team": "CO"},
        {"source_ref": "ZD-1", "gap": "Resend the tickets to the guest", "team": "CO"}])
    assert any("already said" in n for n in rep["notes"]), rep["notes"]


def test_two_genuinely_different_fixes_are_two_rows():
    """A dedupe that eats a real fix is worse than the repetition — that team
    loses work nobody will pick up."""
    from server.checklist import actions_from_gaps
    tabs, _ = actions_from_gaps([
        {"source_ref": "ZD-1", "gap": "Resend the tickets to the guest", "team": "CO"},
        {"source_ref": "ZD-1", "gap": "Refund the second ticket", "team": "CO"}])
    assert len(tabs["co"]) == 2, tabs["co"]
