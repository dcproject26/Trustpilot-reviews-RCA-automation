"""A hand-set scenario wins, sticks, and says when it has gone stale.

"Agreed — option one, override wins and sticks. Two refinements before you
build. Make the badge compare, not just label. ... Override the primary, keep
the overlays."

Three decisions, each with a failure it exists to prevent:

  * OVERRIDE WINS — correcting L1/L2 must not silently discard a deliberate
    judgement about how the case should be read.
  * THE BADGE COMPARES — "set by hand" is provenance; the thing that bites is
    an override whose reason no longer holds. A static tag makes the reader
    reconstruct that later, so the comparison has to fire the moment L1/L2
    moves.
  * OVERLAYS KEEP STACKING — they read booking FACTS. Overriding the primary
    says how to read the case; it does not claim the booking was not
    cancelled.

Driven through the module, not asserted at source: routing is a lookup and a
test that a name appears in a table proves nothing about what reconcile does
with it.
"""
import pytest

from server.checklist import SCENARIO_CHECKS, scenarios_for
from server.scenario_override import (MANUAL, ROUTED, apply, effective_overlays,
                                      is_known, reconcile, routed_primary,
                                      uncovered)

# A pair that actually ROUTES. The first choice here was Operations Issue /
# Ticket Issues, which routes to nothing — 12 of these tests skipped silently
# and the file reported green. The guard below is what caught it, and it stays
# for exactly that reason.
L1, L2 = "Operations Issue", "Meeting Point Issues"

# A pair that routes AND carries static overlays, for the overlay tests.
OVL_L1, OVL_L2 = "Supply Partner Issue", "Seating Issues"


def _two_real_scenarios():
    """A routed primary and a different real scenario to override it with.
    Skips rather than passes if the taxonomy cannot supply both — a test that
    quietly asserts nothing is the failure this repo is built around."""
    routed = routed_primary(L1, L2)
    other = next((s for s in sorted(SCENARIO_CHECKS) if s != routed), None)
    if not routed or not other:
        pytest.skip("taxonomy has no routed primary to override")
    return routed, other


def test_the_fixture_classification_actually_routes():
    """Every test below is meaningless against a pair that routes to None."""
    assert routed_primary(L1, L2), f"{L1}/{L2} routes to nothing"


# ── the override wins and sticks ───────────────────────────────────────────

def test_a_manual_primary_survives_a_classification_change():
    routed, other = _two_real_scenarios()
    got = reconcile(other, MANUAL, L1, L2)
    assert got["primary"] == other, (
        "the override was replaced by routing — correcting a classification "
        "silently discarded a deliberate judgement")


def test_a_routed_primary_follows_the_classification():
    """The other half. Without this an override would be indistinguishable
    from a stale value, and nothing would ever re-route."""
    routed, other = _two_real_scenarios()
    got = reconcile(other, ROUTED, L1, L2)
    assert got["primary"] == routed


def test_a_draft_with_no_recorded_source_is_treated_as_routed():
    """Everything written before this existed was routed by definition.
    Defaulting the other way would badge the whole back catalogue as
    hand-set."""
    routed, other = _two_real_scenarios()
    assert reconcile(other, None, L1, L2)["primary"] == routed
    assert reconcile(other, "", L1, L2)["source"] == ROUTED


def test_a_routed_primary_keeps_its_value_when_routing_has_none():
    """An unroutable pair should not blank a working draft's scenario."""
    got = reconcile("Tickets sent late", ROUTED, "Nonsense L1", "Nonsense L2")
    assert got["primary"] == "Tickets sent late"
    assert got["routed_now"] is None


# ── the badge compares, it does not just label ─────────────────────────────

def test_an_override_matching_current_routing_has_nothing_to_reconcile():
    """"Override matches what routing would now produce → nothing to
    reconcile." A badge that fires here trains the reader to ignore it."""
    routed, _ = _two_real_scenarios()
    got = reconcile(routed, MANUAL, L1, L2)
    assert got["diverged"] is False
    assert got["primary"] == routed


def test_an_override_disagreeing_with_routing_is_flagged():
    routed, other = _two_real_scenarios()
    got = reconcile(other, MANUAL, L1, L2)
    assert got["diverged"] is True


def test_the_card_is_given_both_values_to_show():
    """"show both, plus what routing would say, plus one-click revert" — the
    revert needs a target, and the target is routed_now."""
    routed, other = _two_real_scenarios()
    got = reconcile(other, MANUAL, L1, L2)
    assert got["primary"] == other
    assert got["routed_now"] == routed


def test_a_stale_routed_primary_is_not_reported_as_diverged():
    """A routed value that has fallen behind is not a contradiction to
    reconcile — it is simply re-routed, and the caller does that. Reporting it
    as divergence would put a reconcile prompt on a draft nobody touched."""
    routed, other = _two_real_scenarios()
    got = reconcile(other, ROUTED, L1, L2)
    assert got["diverged"] is False


def test_divergence_is_false_when_routing_has_no_opinion():
    """Nothing to compare against. Showing "routing would say: —" beside an
    override is a contradiction with one side missing."""
    got = reconcile("Tickets sent late", MANUAL, "Nonsense L1", "Nonsense L2")
    assert got["diverged"] is False
    assert got["primary"] == "Tickets sent late"


# ── overlays keep stacking ─────────────────────────────────────────────────

def test_overriding_the_primary_does_not_stop_overlays_stacking():
    """They read booking facts. An override says how to READ the case; it
    does not claim the booking was not cancelled."""
    booking = {"booking_status": "CANCELLED"}
    got = effective_overlays(L1, L2, None, {}, booking, primary="Tickets sent late")
    assert "Unfulfilled booking" in got, got


def test_a_fact_driven_overlay_appears_even_on_an_overridden_draft():
    routed, other = _two_real_scenarios()
    got = apply(L1, L2, None, other, MANUAL,
                booking={"booking_status": "CANCELLED"})
    assert "Unfulfilled booking" in got["overlays"]
    assert got["primary"] == other, "the override was lost while stacking overlays"


def test_the_primary_is_never_repeated_as_its_own_overlay():
    got = effective_overlays(L1, L2, None, {},
                             {"booking_status": "CANCELLED"},
                             primary="Unfulfilled booking")
    assert "Unfulfilled booking" not in got


def test_removing_an_overlay_is_explicit_and_sticks():
    """Overlays are recomputed from facts on every look, so without a record
    of the removal it would come straight back — removal would appear to work
    and undo itself on the next render."""
    booking = {"booking_status": "CANCELLED"}
    assert "Unfulfilled booking" in effective_overlays(L1, L2, None, {}, booking)
    got = effective_overlays(L1, L2, None, {}, booking,
                             removed=["Unfulfilled booking"])
    assert "Unfulfilled booking" not in got


def test_the_overlay_fixture_actually_has_overlays():
    """Same guard as the routing one, for the same reason."""
    assert scenarios_for(OVL_L1, OVL_L2)["overlays"], \
        f"{OVL_L1}/{OVL_L2} routes no static overlays — the tests below skip"


def test_a_static_overlay_survives_an_override_of_the_primary():
    routed = scenarios_for(OVL_L1, OVL_L2)["overlays"]
    got = effective_overlays(OVL_L1, OVL_L2, None, {}, {},
                             primary="Tickets sent late")
    for s in routed:
        assert s in got, s


def test_removing_one_overlay_does_not_remove_the_others():
    routed = scenarios_for(OVL_L1, OVL_L2)["overlays"]
    got = effective_overlays(OVL_L1, OVL_L2, None, {}, {}, removed=[routed[0]])
    assert routed[0] not in got
    for s in routed[1:]:
        assert s in got, s


# ── rule 13 coverage, after an override ────────────────────────────────────

def test_a_scenario_no_guest_issue_covers_is_named():
    """Output rule 13 guarantees coverage at GENERATION time. An override
    applied afterwards breaks it silently — this is what the card flags."""
    got = uncovered(["Tickets sent late"],
                    [{"issue": "Guest was overcharged",
                      "root_cause": "Pricing config"}])
    assert got == ["Tickets sent late"]


def test_a_covered_scenario_is_not_flagged():
    got = uncovered(["Tickets sent late"],
                    [{"issue": "Tickets sent late", "root_cause": "Fulfilment"}])
    assert got == []


def test_coverage_reads_the_root_cause_too():
    """The validator's own definition. Two definitions of "covered" would let
    the card and the confidence trail disagree about the same scenario, and
    the reader has no way to tell which is right."""
    got = uncovered(["Tickets sent late"],
                    [{"issue": "Guest complained",
                      "root_cause": "Tickets sent late by the vendor"}])
    assert got == []


def test_it_matches_the_validator_s_verdict_on_the_same_input():
    """Pinned against the real validator rather than restated, so the two
    cannot drift apart in a later edit."""
    from server.services.rca_v4_validate import validate
    issues = [{"issue": "Guest was overcharged", "root_cause": "Pricing config",
               "claim": "x", "claim_accuracy": "Accurate"}]
    _, notes = validate({"what_went_wrong": {"guest_issues": issues}},
                        ["Tickets sent late"])
    validator_flagged = any("Tickets sent late" in n for n in notes)
    mine_flagged = bool(uncovered(["Tickets sent late"], issues))
    assert validator_flagged == mine_flagged is True


def test_an_uncovered_scenario_is_not_flagged_when_the_rca_names_it():
    """The RCA's own `scenarios` list is the model saying it handled that
    scenario, which the validator already honours."""
    got = uncovered(["Tickets sent late"], [{"issue": "Something else"}],
                    rca_scenarios=["Tickets sent late"])
    assert got == []


def test_apply_reports_the_gap_an_override_opens():
    routed, other = _two_real_scenarios()
    got = apply(L1, L2, None, other, MANUAL,
                guest_issues=[{"issue": "Guest was overcharged"}])
    assert other in got["uncovered"], (
        "an override applied after generation left a scenario with no issue "
        "behind it and nothing said so")


# ── one entry point ────────────────────────────────────────────────────────

def test_apply_returns_everything_the_card_needs():
    routed, other = _two_real_scenarios()
    got = apply(L1, L2, None, other, MANUAL)
    for k in ("primary", "routed_now", "source", "diverged", "overlays",
              "effective", "uncovered"):
        assert k in got, k


def test_the_effective_list_leads_with_the_primary():
    """`scenarios_routed` is positional downstream — the first entry is the
    primary everywhere else in the pipeline."""
    routed, other = _two_real_scenarios()
    got = apply(L1, L2, None, other, MANUAL,
                booking={"booking_status": "CANCELLED"})
    assert got["effective"][0] == other


def test_an_unknown_scenario_is_recognisable_as_unroutable():
    """An override to a name no checklist knows produces an RCA with nothing
    behind it. The caller needs to be able to tell."""
    assert is_known("Tickets sent late") is True
    assert is_known("Something nobody defined") is False
    assert is_known(None) is False
