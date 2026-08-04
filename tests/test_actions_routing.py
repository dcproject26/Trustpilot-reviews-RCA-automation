"""Actions taken go to the team that can action them.

"any refund related issues or guest claims against sp will be in SP chip ...
if there is CE chats not handled properly then to flag that there. if
something is from process gap to flag it here as well."

First match wins, so the order of _OWNER_RULES IS the routing. Two families
were filed wrong and neither was visible from the card:

  * REFUNDS went to Customer. A refund is a claim against the supply partner,
    and the team who could action it never saw it.
  * EXPERIENCE problems — redemption, quality, the guide, the venue — went to
    Business, which cannot action any of them.

Driven through `_owner_for_action`, because a keyword present in a list proves
nothing about which rule matches first.
"""
import pytest

from server.checklist import _owner_for_action, actions_for


@pytest.mark.parametrize("text", [
    "Refund the guest in full",
    "Process the refund per DSS",
    "Raise the chargeback with the operator",
    "Share the ARN with the guest",
])
def test_a_refund_is_a_claim_against_the_supply_partner(text):
    assert _owner_for_action(text) == "sp", (
        f"{text!r} routed to {_owner_for_action(text)!r} — the team who can "
        f"action a refund claim never sees it")


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
    "CE chat not handled properly",
    "Chat was mishandled — flag to CE",
    "Process gap: no follow up within 48-72h",
    "Tag the ticket per DSS",
    "Add internal notes for the next agent",
])
def test_ce_mishandling_and_process_gaps_are_flagged_to_ce(text):
    assert _owner_for_action(text) == "ce", \
        f"{text!r} routed to {_owner_for_action(text)!r}"


@pytest.mark.parametrize("text,owner", [
    ("Check inventory with IO",          "business"),
    ("Escalate to the escalation team",  "business"),
    ("Raise with tech team",             "product"),
    ("BMS shows no fulfilment row",      "product"),
    ("Resend tickets to the guest",      "customer"),
    ("Reschedule for the guest",         "customer"),
])
def test_the_other_tabs_keep_what_is_genuinely_theirs(text, owner):
    """Widening SP must not swallow everything — a chip that catches all of it
    is the same as no routing."""
    assert _owner_for_action(text) == owner, \
        f"{text!r} routed to {_owner_for_action(text)!r}, expected {owner}"


def test_an_unroutable_action_has_no_owner():
    """"dont make up stuff if nothing is there, leave it blank." An action with
    no owner is a pure check, and putting it on an arbitrary chip asks a team
    to action something that was never theirs."""
    assert _owner_for_action("Confirm the visit date") is None
    assert _owner_for_action("") is None


def test_actions_for_drops_it_rather_than_parking_it_somewhere(monkeypatch):
    """The other end, and the one that matters: `_owner_for_action` returning
    None proves nothing about what `actions_for` DOES with a None.

    A mutation making the fallback "ce" survived every test in this file,
    because they all drove the routing function and none drove the thing that
    builds the tabs. An action nobody owns would have been filed to CE on
    every card, and CE would be reading work that was never theirs.
    """
    import server.checklist as ck
    monkeypatch.setattr(ck, "scenario_actions",
                        lambda name: ["Confirm the visit date",
                                      "Refund the guest in full"])
    got = ck.actions_for(["anything"])
    flat = [a for items in got.values() for a in items]
    assert "Refund the guest in full" in got["sp"]
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
    assert set(got) == {"sp", "customer", "business", "product", "ce"}
    assert all(v == [] for v in got.values()), got


def test_an_unknown_scenario_produces_no_invented_actions():
    got = actions_for(["A scenario nobody defined"])
    assert all(v == [] for v in got.values()), got


def test_a_real_scenario_produces_actions_on_some_tab():
    """The guard on the guards above: if scenario_actions returned nothing for
    everything, every routing test would still pass and the section would be
    empty on every card."""
    from server.checklist import SCENARIO_CHECKS
    any_action = False
    for name in sorted(SCENARIO_CHECKS):
        if any(actions_for([name]).values()):
            any_action = True
            break
    assert any_action, "no scenario produces a routed action at all"


def test_the_same_action_is_not_listed_twice_across_scenarios():
    from server.checklist import SCENARIO_CHECKS
    names = sorted(SCENARIO_CHECKS)[:6]
    got = actions_for(names)
    flat = [a for items in got.values() for a in items]
    assert len(flat) == len({a.strip().lower() for a in flat}), flat
