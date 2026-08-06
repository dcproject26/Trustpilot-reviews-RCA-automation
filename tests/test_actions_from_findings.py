"""Actions Taken carries what this case found — not the scenario's playbook.

The section used to be sourced from the DSS guideline sheet for the routed
scenario, then filtered twice: by flagged team, and by word overlap with the
findings. Two filters on the wrong source. Guideline rows are a PLAYBOOK for a
scenario, not things that happened on this booking, so a card with no delayed
refund and no BMS error carried:

    Refund-done tags on ZD updated + refund status on checkout?
    Refund done within promised timeframe?
    BMS refund error -> raise with Leads on #co-issue or Fin on priority
    Share ARN number for delayed refunds

Every one a valid row for the scenario. Every one passing both filters. Every
one a statement about work nobody did, on a section a reader takes to mean
"this is what we did".

SIX SOURCES, all of them already findings on the card: the flags, each issue's
operational failure, its SOP gap, its fix (which names the owning team), the
provenance-checked improvement points, and the DSS MISS.

DSS CONTRIBUTES ONE THING: what the next escalation step should have been,
where it did not happen. Not an anchor, not a definition, not a comment, and
no rows of its own.
"""
import pytest

from server.checklist import (ACTION_TEAMS, actions_from_findings,
                              _ALL_GUIDELINE_ACTIONS)


def _flag(team="co", text="No follow-up after the first reply"):
    return {"team": team.upper(), "flag": text, "evidence": "ZD-1"}


def _issue(**kw):
    base = {"issue": "Tickets arrived late",
            "operational_failure": "Nobody watched the fulfilment queue",
            "sop_gap": "No alert exists for a stalled fulfilment run",
            "fix": {"action": "Add an alert on a stalled fulfilment run",
                    "owner": "CO"}}
    base.update(kw)
    return base


def _flat(tabs):
    return [a for v in tabs.values() for a in v]


# ── the six sources ────────────────────────────────────────────────────────

def test_a_flag_becomes_a_row_on_its_own_team():
    tabs, _ = actions_from_findings([], [_flag("sp", "Vendor never replied")])
    assert tabs["sp"] == ["Vendor never replied"]


def test_the_operational_failure_the_sop_gap_and_the_fix_all_appear():
    tabs, _ = actions_from_findings([_issue()], [_flag()])
    co = tabs["co"]
    assert "Nobody watched the fulfilment queue" in co, co
    assert "No alert exists for a stalled fulfilment run" in co, co
    assert "Add an alert on a stalled fulfilment run" in co, co


def test_the_fix_owner_routes_its_issues_findings():
    """The fix is the only one of the three that names a team, so it routes
    the failure and the gap it belongs with."""
    tabs, _ = actions_from_findings(
        [_issue(fix={"action": "Chase the vendor at 30 minutes",
                     "owner": "SP"})],
        [_flag("co"), _flag("biz", "Repeat complaint")])
    assert "Nobody watched the fulfilment queue" in tabs["sp"], tabs["sp"]


def test_an_improvement_point_is_routed_by_the_finding_it_cites():
    """AOI rows carry {point, from, source} and no team. Reading a team off
    them would send every point to the unrouted pile — six findings reported
    as unplaceable on a card where all six could be placed."""
    tabs, _ = actions_from_findings(
        [_issue()],
        [_flag("sp", "Vendor never replied to the chase")],
        improvements=[{"point": "Add a vendor response SLA",
                       "from": "flag",
                       "source": "Vendor never replied to the chase"}])
    assert "Add a vendor response SLA" in tabs["sp"], tabs["sp"]


def test_the_dss_miss_lands_on_the_team_that_would_have_taken_the_step():
    tabs, _ = actions_from_findings(
        [], [_flag("co")],
        dss_miss=[{"team": "SP",
                   "action": "Escalate to SP leads after 30 minutes"}])
    assert tabs["sp"] == ["Escalate to SP leads after 30 minutes"]


# ── and nothing else ───────────────────────────────────────────────────────

def test_no_guideline_row_can_appear():
    """The reported bug. The sheet is not a source, so none of its rows may
    reach the card by any route."""
    tabs, _ = actions_from_findings([_issue()], [_flag()])
    assert not (set(_flat(tabs)) & _ALL_GUIDELINE_ACTIONS), _flat(tabs)


def test_nothing_is_invented_every_row_is_a_string_from_the_findings():
    issue = _issue()
    flag = _flag()
    tabs, _ = actions_from_findings([issue], [flag])
    allowed = {flag["flag"], issue["operational_failure"], issue["sop_gap"],
               issue["fix"]["action"]}
    assert set(_flat(tabs)) <= allowed, set(_flat(tabs)) - allowed


def test_a_case_with_no_findings_raises_nothing_and_says_why():
    tabs, report = actions_from_findings([], [])
    assert _flat(tabs) == []
    assert any("nothing this case found" in n for n in report["notes"]), report


# ── deduplication ──────────────────────────────────────────────────────────

def test_the_same_finding_worded_twice_is_one_row():
    """A flag and an operational failure routinely say one thing two ways.
    Printing both reads as two pieces of work."""
    tabs, _ = actions_from_findings(
        [_issue(operational_failure="Nobody watched the fulfilment queue")],
        [_flag("co", "Nobody watched the fulfilment queue")])
    assert tabs["co"].count("Nobody watched the fulfilment queue") == 1


def test_near_duplicates_collapse_too():
    tabs, _ = actions_from_findings(
        [_issue(operational_failure="Nobody watched the fulfilment queue")],
        [_flag("co", "The fulfilment queue was not watched by anybody")])
    assert len([a for a in tabs["co"] if "fulfilment queue" in a]) == 1, tabs["co"]


def test_a_merge_is_announced_because_it_is_a_judgement():
    _, report = actions_from_findings(
        [_issue(operational_failure="Nobody watched the fulfilment queue")],
        [_flag("co", "Nobody watched the fulfilment queue")])
    assert any("already said" in n for n in report["notes"]), report


def test_two_genuinely_different_findings_are_not_merged():
    """The inverse bug, and just as bad: an over-eager merge hides work."""
    tabs, _ = actions_from_findings(
        [], [_flag("co", "No follow-up after the first reply"),
             _flag("co", "Refund was issued to the wrong payment method")])
    assert len(tabs["co"]) == 2, tabs["co"]


def test_short_rows_are_compared_exactly_rather_than_by_overlap():
    """With one or two content words there is not enough to judge overlap on,
    and collapsing them would drop distinct findings sharing a word."""
    tabs, _ = actions_from_findings(
        [], [_flag("co", "Late refund"), _flag("co", "Late voucher")])
    assert len(tabs["co"]) == 2, tabs["co"]


# ── routing that declines rather than guesses ──────────────────────────────

def test_a_finding_with_no_team_falls_to_the_only_flagged_team():
    """One flagged team is an unambiguous answer, not a guess."""
    tabs, _ = actions_from_findings(
        [_issue(fix=None)], [_flag("sp", "Vendor never replied")])
    assert "Nobody watched the fulfilment queue" in tabs["sp"], tabs["sp"]


def test_with_two_flagged_teams_an_unowned_finding_is_reported_not_guessed():
    """A row parked on the wrong tab accuses a team that did nothing wrong."""
    tabs, report = actions_from_findings(
        [_issue(fix=None)], [_flag("co"), _flag("sp", "Vendor silent")])
    assert "Nobody watched the fulfilment queue" not in _flat(tabs)
    assert any("could not be routed" in n for n in report["notes"]), report


def test_the_unrouted_report_names_what_was_dropped():
    """CLAUDE.md §1: counted AND named. A bare number leaves the reader unable
    to tell which finding is missing."""
    _, report = actions_from_findings(
        [_issue(fix=None)], [_flag("co"), _flag("sp", "Vendor silent")])
    said = " ".join(report["notes"])
    assert "fulfilment queue" in said, said


def test_an_unreadable_team_code_does_not_silently_become_a_real_team():
    tabs, report = actions_from_findings(
        [], [{"team": "WHO", "flag": "something", "evidence": "e"},
             {"team": "ALSO_WHO", "flag": "another", "evidence": "e"}])
    assert _flat(tabs) == []
    assert any("could not be routed" in n for n in report["notes"]), report


# ── shape and hand-typed rows ──────────────────────────────────────────────

def test_every_tab_key_is_present_even_when_empty():
    """An absent key and an empty list render the same and mean different
    things: one is a tab with nothing raised, the other a tab that was
    forgotten."""
    tabs, _ = actions_from_findings([], [])
    assert set(tabs) == set(ACTION_TEAMS)


def test_a_hand_typed_row_survives_a_rebuild():
    """The section is recomputed on every run, so a row a person added is only
    kept by this door. Losing it discards the work of deciding it mattered."""
    tabs, report = actions_from_findings(
        [], [_flag()], keep={"co": ["Rang the vendor myself on the day"]})
    assert "Rang the vendor myself on the day" in tabs["co"]
    assert report["kept"] == 1


def test_a_hand_typed_row_that_repeats_a_finding_is_not_added_twice():
    tabs, _ = actions_from_findings(
        [], [_flag("co", "No follow-up after the first reply")],
        keep={"co": ["No follow-up after the first reply"]})
    assert tabs["co"].count("No follow-up after the first reply") == 1


def test_a_guideline_row_cannot_return_through_the_keep_door():
    """`keep` carries the previous tabs. A card built before this change holds
    guideline rows, and they must not come back by the side entrance."""
    row = sorted(_ALL_GUIDELINE_ACTIONS)[0]
    tabs, _ = actions_from_findings([], [_flag()], keep={"co": [row]})
    assert row not in tabs["co"], row
