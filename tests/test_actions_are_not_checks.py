"""Actions Taken records what was done. A question is not something done.

The SP tab carried four rows, two of them questions:

    Refund-done tags on ZD updated + refund status on checkout?
    Refund done within promised timeframe?

Under a heading claiming to record completed work, the dashboard was asking
the reader whether the work had happened. "We did this" and "someone should
check whether this was done" were rendered identically, in the same list, with
the same tick.

The Guidelines sheet mixes checks and actions, and the owner routing matched
both because both mention "refund". Shape is what separates them: a check is
phrased as a question, or as a bare verify/confirm/check instruction.
"""
import pytest

from server.checklist import is_check, _owner_for_action


@pytest.mark.parametrize("row", [
    "Refund-done tags on ZD updated + refund status on checkout?",
    "Refund done within promised timeframe?",
    "Verify the guest was informed",
    "Confirm the refund reached the guest",
    "Check that the ARN was shared",
    "Ensure tags are applied",
    "Did CE escalate to a lead?",
    "Is the booking non-refundable?",
])
def test_a_check_is_recognised(row):
    assert is_check(row) is True, f"{row!r} would be listed as an action taken"


@pytest.mark.parametrize("row", [
    "BMS refund error → raise with Leads on #co-issue or Fin on priority",
    "Share ARN number for delayed refunds",
    "Raise with SP for redemption failure",
    "Refund the booking fee",
    "Email the guest with the corrected entry time",
])
def test_an_action_is_not_mistaken_for_a_check(row):
    assert is_check(row) is False, (
        f"{row!r} reads as a check — a false positive here DELETES real work "
        f"from the tab, which is worse than the bug being fixed")


@pytest.mark.parametrize("row", [
    "Refund-done tags on ZD updated + refund status on checkout?",
    "Refund done within promised timeframe?",
])
def test_a_check_is_routed_to_nobody(row):
    """It must not merely be labelled — it must not reach a tab. Both of these
    mention "refund", so the SP rule matches them on keywords alone."""
    assert _owner_for_action(row) is None


def test_the_real_dss_steps_still_route_to_sp():
    """The other half. A filter that emptied the tab would "fix" the screenshot
    and lose the two rows that belonged there."""
    assert _owner_for_action(
        "BMS refund error → raise with Leads on #co-issue or Fin on priority") == "sp"
    assert _owner_for_action("Share ARN number for delayed refunds") == "sp"


def test_an_empty_row_is_not_a_check():
    """Nothing is not a question. It is nothing, and it is dropped for being
    unroutable rather than for being a check — different reasons, and only one
    of them would be worth reporting."""
    assert is_check("") is False
    assert is_check(None) is False


# ── owner routing keywords that nothing else covers ────────────────────────

@pytest.mark.parametrize("row,owner", [
    ("Review the pricing on this TGID", "business"),
    ("Raise the commercial terms with the BDM", "business"),
    ("Raise with SP for the venue issue", "sp"),
    ("Refund the guest", "sp"),
    ("CE mishandled the case - retrain on the macro", "ce"),
    ("Website checkout bug - raise with tech", "product"),
])
def test_the_routing_keywords_reach_their_team(row, owner):
    """A mutation dropping "pricing" and "commercial" from the business rule
    survived the whole suite: nothing asserted where a pricing action goes,
    so it could silently route to nobody and vanish from every tab."""
    assert _owner_for_action(row) == owner, (
        f"{row!r} routes to {_owner_for_action(row)!r} — an action that "
        f"routes to nobody is dropped from the card entirely")
