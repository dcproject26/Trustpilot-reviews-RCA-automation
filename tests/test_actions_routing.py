"""Actions taken go to the team that can action them — and only when flagged.

The five old tabs (SP / Customer / Business / CE / Product) were not teams.
"Customer" is not one, "Business" was inventory, pricing and the escalation
ladder at once, and a catalog problem or a refund Finance has to execute had
nowhere to go at all. Nine replace them, given by the ORM team:

    NA/Guest error · Supply Partner · Content/Catalog/Media team · CO team ·
    Tech team · Inventory Team · Product team · Biz team · Finance team

Two rules, and the second is the one that changes what the card says:

  * A row appears because the DSS GUIDELINES say it must be raised AND because
    it has been FLAGGED. Both. Guidelines alone put the same generic list on
    every card of a given L2; flags alone invent work no playbook asks for.
  * Nothing invented. No flag, no row.

Driven through `_owner_for_action` and `actions_raised`, because a keyword
present in a list proves nothing about which rule matches first, and a routing
function proves nothing about what the thing building the tabs DOES with it.
"""
import pytest

from server.checklist import (ACTION_TEAMS, SCENARIO_CHECKS, _owner_for_action,
                              actions_for, actions_raised, team_of_flag)


def _flag(team):
    return {"team": team, "flag": "something went wrong", "evidence": "a fact"}


# ── the nine, and what each one is for ──────────────────────────────────────

def test_the_nine_teams_are_the_vocabulary():
    assert ACTION_TEAMS == ("guest", "sp", "content", "co", "tech",
                            "inventory", "product", "biz", "finance")


@pytest.mark.parametrize("text", [
    "Raise redemption issue with SP",
    "Guest was turned away at the venue",
    "Poor guide quality on the tour",
    "Meeting point was wrong",
    "Voucher would not scan",
    "Booking was overbooked by the operator",
])
def test_an_experience_problem_is_the_supply_partner_s(text):
    assert _owner_for_action(text) == "sp", \
        f"{text!r} routed to {_owner_for_action(text)!r}"


@pytest.mark.parametrize("text", [
    # HANDOFF §2, decided: a missing or wrong VARIANT, PAX TYPE, INCLUSION or
    # PAGE STATEMENT is Content/Catalog/Media. The booking flow renders whatever
    # pax types the catalog defines, so a missing option is a configuration
    # fault and not a flow fault.
    "Child ticket/pax-type concerns → relevant team",
    "No Baby/Infant pax type exists for this TGID",
    "Cross-check final prices (incl. fees), variant, inclusions vs venue pricing",
    "Recurring issues for same TID/VID → add callouts",
    "Redemption details clear on voucher/email? If missing → BizOps via Retool/CO assistant",
])
def test_a_catalog_or_page_problem_is_content(text):
    assert _owner_for_action(text) == "content", (
        f"{text!r} routed to {_owner_for_action(text)!r} — a missing pax type "
        f"is the catalog's, and Product cannot fix it")


@pytest.mark.parametrize("text", [
    "The checkout flow errored before payment",
    "The booking flow showed the wrong date",
    "App issue on the in-app voucher screen",
])
def test_the_flow_failing_with_a_correct_catalog_is_product(text):
    """The other half of §2. If Content swallowed everything the flow does,
    Product would never be raised and the tab would be decoration."""
    assert _owner_for_action(text) == "product", \
        f"{text!r} routed to {_owner_for_action(text)!r}"


@pytest.mark.parametrize("text,owner", [
    ("Raise with Inventory/Business/SP by FF type (prepurchase→IO)", "inventory"),
    ("Escalate to IO/Tech if needed",                                "inventory"),
    ("Raise with Tech for BMS/PDF/app issues",                       "tech"),
    ("If clarity fails, raise with tech team",                       "tech"),
    ("BMS refund error → raise with Leads on #co-issue or Fin on priority", "finance"),
    ("Share ARN number for delayed refunds",                         "finance"),
    ("If valid, partial refund of the difference to bank account",   "finance"),
    ("Raise with BizOps and BDM",                                    "biz"),
    ("Major/recurring → Escalation team",                            "biz"),
    ("Tag RO/CE error for any FF issue",                             "co"),
    ("Email guest with proof of when tickets were sent",             "co"),
    ("Resend tickets (future DOV) or refund/credits per DSS",        "co"),
    ("Double booking → check time gap + DSS guidelines",             "guest"),
])
def test_each_team_gets_what_only_it_can_action(text, owner):
    assert _owner_for_action(text) == owner, (
        f"{text!r} routes to {_owner_for_action(text)!r}, expected {owner} — "
        f"a row filed with a team that cannot action it is work nobody does")


def test_an_unroutable_action_has_no_owner():
    """"dont make up stuff if nothing is there, leave it blank." An action with
    no owner is a pure check, and putting it on an arbitrary chip asks a team
    to action something that was never theirs."""
    assert _owner_for_action("Confirm the visit date") is None
    assert _owner_for_action("") is None


def test_actions_for_drops_it_rather_than_parking_it_somewhere(monkeypatch):
    """The other end, and the one that matters: `_owner_for_action` returning
    None proves nothing about what `actions_for` DOES with a None.

    A mutation making the fallback "co" survived every test in this file,
    because they all drove the routing function and none drove the thing that
    builds the tabs. An action nobody owns would have been filed to CO on
    every card, and CO would be reading work that was never theirs.
    """
    import server.checklist as ck
    monkeypatch.setattr(ck, "scenario_actions",
                        lambda name: ["Confirm the visit date",
                                      "Raise redemption issue with SP"])
    got = ck.actions_for(["anything"])
    flat = [a for items in got.values() for a in items]
    assert "Raise redemption issue with SP" in got["sp"]
    assert "Confirm the visit date" not in flat, (
        f"a pure check was assigned an owner: {got}")


def test_a_wholly_unroutable_scenario_leaves_every_tab_empty(monkeypatch):
    import server.checklist as ck
    monkeypatch.setattr(ck, "scenario_actions",
                        lambda name: ["Confirm the visit date",
                                      "Check the booking status"])
    got = ck.actions_for(["anything"])
    assert all(v == [] for v in got.values()), got


def test_nothing_routed_leaves_every_tab_empty():
    got = actions_for([])
    assert set(got) == set(ACTION_TEAMS)
    assert all(v == [] for v in got.values()), got


def test_an_unknown_scenario_produces_no_invented_actions():
    got = actions_for(["A scenario nobody defined"])
    assert all(v == [] for v in got.values()), got


def test_a_real_scenario_produces_actions_on_some_tab():
    """The guard on the guards above: if scenario_actions returned nothing for
    everything, every routing test would still pass and the section would be
    empty on every card."""
    any_action = False
    for name in sorted(SCENARIO_CHECKS):
        if any(actions_for([name]).values()):
            any_action = True
            break
    assert any_action, "no scenario produces a routed action at all"


def test_the_same_action_is_not_listed_twice_across_scenarios():
    names = sorted(SCENARIO_CHECKS)[:6]
    got = actions_for(names)
    flat = [a for items in got.values() for a in items]
    assert len(flat) == len({a.strip().lower() for a in flat}), flat


def test_every_scenario_routes_somewhere_or_nowhere_but_never_off_the_nine():
    """A tab key outside the nine renders nothing at all — the chip row is
    built from the nine, so a tenth key is a row that exists in the data and
    on no screen."""
    for name in sorted(SCENARIO_CHECKS):
        assert set(actions_for([name])) == set(ACTION_TEAMS), name


# ── the AND: guidelines say it, and a flag names the team ───────────────────

def test_a_guideline_action_is_raised_only_when_its_team_is_flagged():
    scen = ["Tickets sent late"]
    ungated = actions_for(scen)
    assert ungated["co"], "fixture no longer produces a CO guideline action"

    tabs, _ = actions_raised(scen, [_flag("CO")])
    assert tabs["co"] == ungated["co"]
    assert all(not v for k, v in tabs.items() if k != "co"), tabs


def test_nothing_flagged_raises_nothing_however_much_the_guidelines_prescribe():
    """Guidelines alone would put the same list on every card of this L2, and
    the section is read as "this is what we did"."""
    scen = ["Tickets sent late"]
    assert any(actions_for(scen).values()), "fixture prescribes nothing"
    tabs, report = actions_raised(scen, [])
    assert all(not v for v in tabs.values()), tabs
    assert report["raised"] == 0 and report["withheld"] > 0


def test_a_flag_alone_invents_nothing():
    """The other direction. A team flagged on a scenario whose guidelines
    prescribe nothing for them gets no row — the card would otherwise claim we
    raised something no playbook asks for."""
    tabs, report = actions_raised(["Untraceable booking"], [_flag("FINANCE")])
    assert all(not v for v in tabs.values()), tabs
    assert report["flagged_teams"] == ["finance"]


def test_the_withheld_rows_are_counted_and_named():
    """CLAUDE.md rule 1. A tab empty because nothing was flagged and a tab
    empty because the guidelines prescribe nothing are the same blank space,
    and they mean opposite things — so the run has to say which."""
    _, report = actions_raised(["Tickets sent late"], [_flag("CO")])
    assert report["withheld"] > 0
    assert report["withheld_teams"], report
    note = " ".join(report["notes"])
    assert "withheld" in note
    for team in report["withheld_teams"]:
        from server.taxonomy import ACTION_TABS
        assert ACTION_TABS[team]["label"] in note, note


def test_a_run_with_nothing_to_raise_says_which_kind_of_nothing():
    no_scenario, _ = actions_raised([], [_flag("CO")])[0], None
    _, r_noscen = actions_raised([], [_flag("CO")])
    _, r_nochecks = actions_raised(["Untraceable booking"], [_flag("CO")])
    _, r_noflags = actions_raised(["Tickets sent late"], [])
    assert "no scenario routed" in " ".join(r_noscen["notes"])
    assert "no guideline action" in " ".join(r_nochecks["notes"])
    assert "nothing was flagged" in " ".join(r_noflags["notes"])
    # Three different sentences for three different states, which is the point.
    assert len({tuple(r["notes"]) for r in
                (r_noscen, r_nochecks, r_noflags)}) == 3


def test_a_clean_run_is_quiet():
    """The inverse bug: a note on every healthy run is how a trail stops being
    read. Everything the guidelines prescribe was raised, so there is nothing
    to report — the rows themselves are the report.

    `findings` carries the guideline rows' own wording, so the third condition
    passes every row. Anything less and this test would be asserting that the
    relevance filter is silent about work it withheld.
    """
    scen = ["Tickets sent late"]
    guideline = actions_for(scen)
    flags = [_flag(t.upper()) for t, items in guideline.items() if items]
    findings = " ".join(r for items in guideline.values() for r in items)
    _, report = actions_raised(scen, flags, findings=findings)
    assert report["withheld"] == 0
    assert report["irrelevant"] == 0, report["notes"]
    assert report["relevance_checked"] is True
    assert report["notes"] == [], report["notes"]


def test_not_asking_for_relevance_is_not_the_same_as_finding_none():
    """A caller that omits `findings` gets the two-condition behaviour, and
    the report SAYS the third was never applied. A filter that did not run and
    a filter that ran and withheld nothing must not read alike."""
    scen = ["Tickets sent late"]
    flags = [_flag(t.upper()) for t, items in actions_for(scen).items() if items]
    _, report = actions_raised(scen, flags)
    assert report["relevance_checked"] is False
    assert report["irrelevant"] == 0
    assert any("was NOT checked" in n for n in report["notes"]), report["notes"]


# ── the flag is what carries the team, so reading it has to be exact ────────

@pytest.mark.parametrize("given,expect", [
    ("SP", "sp"), ("content", "content"), ("FINANCE", "finance"),
    # The old vocabulary. CE and RO were both the support desk.
    ("CE", "co"), ("RO", "co"), ("BUSINESS", "biz"),
    # Not a team, and must not become one.
    ("OTHER", ""), ("", ""), ("Marketing", ""),
])
def test_a_flag_names_its_team_or_names_nothing(given, expect):
    assert team_of_flag({"team": given}) == expect


def test_an_unreadable_team_raises_nothing_rather_than_guessing():
    tabs, report = actions_raised(["Tickets sent late"], [_flag("Marketing")])
    assert all(not v for v in tabs.values()), tabs
    assert report["flagged_teams"] == []
