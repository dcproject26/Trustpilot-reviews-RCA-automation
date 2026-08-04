"""A hand-set scenario wins, sticks, and says when it has gone stale.

Scenarios are normally DERIVED: L1/L2/sub-theme route to a primary, and
`compute_overlay_scenarios()` stacks more from booking facts. An analyst can
override the primary, and that override survives a later change to L1/L2 —
otherwise correcting a classification would silently discard a deliberate
judgement.

But an override that outlives its reason is the thing that actually bites. The
failure is not "somebody overrode this", it is "somebody overrode this for a
reason that no longer holds". A static `manual` tag makes the reader
reconstruct that months later; a COMPARISON surfaces the contradiction the
moment L1/L2 moves, while they still remember why. So `reconcile()` returns
what routing would say NOW alongside the stored value, and whether the two
disagree — the card shows nothing when they agree and both when they do not.

Two things it deliberately does not do:

  * It does not touch overlays. `compute_overlay_scenarios()` reads booking
    FACTS — a cancelled status, a fulfilment that never ran. Overriding the
    primary says how the case should be READ; it does not claim the booking
    was not cancelled. Overlays keep stacking, and removing one is its own
    explicit action recorded in `overlays_removed`.
  * It does not decide whether the RCA is stale. Output rule 13 requires every
    routed scenario to be covered by a guest issue, and an override applied
    AFTER generation breaks that guarantee silently — so `uncovered()` names
    the gap for the card to flag, the same way the confidence trail already
    flags a routed-but-uncovered scenario. Regeneration stays available and
    stays the analyst's call.
"""
from server.checklist import (SCENARIO_CHECKS, compute_overlay_scenarios,
                              scenarios_for)

# What `source` can be. Absent means routed: every draft written before this
# existed was routed by definition, and defaulting the other way would badge
# the entire back catalogue as hand-set.
ROUTED, MANUAL = "routed", "manual"


def routed_primary(l1, l2, sub_theme=None):
    """The primary scenario routing gives for this classification, or None."""
    return scenarios_for(l1 or "", l2 or "", sub_theme)["primary"]


def is_known(scenario) -> bool:
    """A scenario the checklists actually know. An override to a name nothing
    routes to would produce an RCA with no checklist behind it."""
    return bool(scenario) and scenario in SCENARIO_CHECKS


def reconcile(stored_primary, source, l1, l2, sub_theme=None) -> dict:
    """How the stored primary stands against what routing says now.

    Returns:
      primary     what to use — the override where there is one, else routing
      routed_now  what routing would produce for this classification
      source      "manual" or "routed"
      diverged    True only when a MANUAL primary disagrees with routing now

    `diverged` is deliberately False for a routed primary that has fallen
    behind: that is not a contradiction to reconcile, it is a value that
    should simply be re-routed, and the caller does so.
    """
    now = routed_primary(l1, l2, sub_theme)
    manual = (source == MANUAL) and bool(stored_primary)

    if not manual:
        # Routed primaries follow the classification. Falling back to the
        # stored value when routing has nothing keeps a working draft working
        # rather than blanking the field on an unroutable L1/L2 pair.
        return {"primary": now or (stored_primary or None),
                "routed_now": now, "source": ROUTED, "diverged": False}

    return {"primary": stored_primary, "routed_now": now, "source": MANUAL,
            # Nothing to reconcile when the override happens to match what
            # routing would now produce. Showing a badge there trains the
            # reader to ignore the badge.
            "diverged": bool(now) and now != stored_primary}


def effective_overlays(l1, l2, sub_theme=None, ticket_facts=None, booking=None,
                       primary=None, removed=None) -> list:
    """Overlays for this draft: stacked from facts, minus the ones removed.

    Recomputed rather than stored, because the facts they read can change —
    a booking cancelled after generation should stack its overlay on the next
    look. `removed` is what stops that resurrecting an overlay somebody
    deliberately took off; without it, removal would appear to work and undo
    itself on the next render.
    """
    overlays = compute_overlay_scenarios(l1 or "", l2 or "", sub_theme,
                                         ticket_facts or {}, booking or {})
    gone = set(removed or [])
    return [s for s in overlays if s != primary and s not in gone]


def _covered_text(guest_issues) -> str:
    """The same haystack rca_v4_validate builds. One definition of "covered",
    because two would let the card and the trail disagree about the same
    scenario — and the reader has no way to tell which is right."""
    return " ".join(((i or {}).get("issue") or "") + " " +
                    ((i or {}).get("root_cause") or "")
                    for i in (guest_issues or []) if isinstance(i, dict)).lower()


def uncovered(scenarios, guest_issues, rca_scenarios=None) -> list:
    """Scenarios with no guest issue behind them.

    Output rule 13 makes this a guarantee at generation time. An override
    applied afterwards breaks it in one of two directions, both silent: the
    RCA covers a scenario the card no longer shows, or the card shows one no
    issue addresses. This names the second, which is the one a reader can act
    on.
    """
    text = _covered_text(guest_issues)
    named = set(rca_scenarios or [])
    return [s for s in (scenarios or [])
            if s and s.lower() not in text and s not in named]


def apply(draft_l1, draft_l2, sub_theme, stored_primary, source,
          ticket_facts=None, booking=None, removed=None,
          guest_issues=None, rca_scenarios=None) -> dict:
    """Everything the card needs about routing, in one call.

    One entry point so the dashboard, the regenerate endpoint and the draft
    payload cannot each compute a slightly different answer — which is how the
    TGID rating tile ended up showing TID+VID data under a TGID label.
    """
    r = reconcile(stored_primary, source, draft_l1, draft_l2, sub_theme)
    overlays = effective_overlays(draft_l1, draft_l2, sub_theme, ticket_facts,
                                  booking, r["primary"], removed)
    effective = [s for s in [r["primary"], *overlays] if s]
    return {**r,
            "overlays": overlays,
            "effective": effective,
            "uncovered": uncovered(effective, guest_issues, rca_scenarios)}
