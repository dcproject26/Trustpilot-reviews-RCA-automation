"""Four corrections taken from a 500-review labelled sample.

Measured on that sample before and after:
    L1     96.0% -> 97.8%
    L1+L2  93.4% -> 95.2%
    +sub   73.8% -> 78.2%

The sample is the evidence, not the test. These pin the MECHANISM each
correction relies on — a registered framework, a rule that reaches the model —
because the accuracy number is a property of 500 particular reviews and cannot
be re-measured here without a live model call.
"""
import pytest

import server.taxonomy as t
from server.taxonomy import (SUB_THEME_REGISTRY, has_sub_theme_framework,
                             is_valid_sub_theme)
from server import prompts


# ── 1. the registry gap that made a question unanswerable ───────────────────

@pytest.mark.parametrize("l2", ["Seating Issues", "Food & Catering"])
def test_the_last_two_sp_l2s_have_a_framework(l2):
    """THE POINT. These were the only Supply Partner pairs with no framework,
    so `has_sub_theme_framework` was false and the validator nulled every
    sub-theme on them — which reads as a model that would not answer rather
    than a question nobody could answer."""
    assert has_sub_theme_framework("Supply Partner Issue", l2)
    assert is_valid_sub_theme("Supply Partner Issue", l2,
                              "G. Other Supply Partner Issue"), (
        "CX labelled every Seating and Food review with the SP catchall; if "
        "that label is not valid here the registration achieved nothing")


@pytest.mark.parametrize("l2", ["Seating Issues", "Food & Catering"])
def test_they_reuse_the_sp_framework_rather_than_a_new_one(l2):
    """Registration, not invention. A parallel set of themes for these two
    would be a second vocabulary for the same L1."""
    assert SUB_THEME_REGISTRY[("Supply Partner Issue", l2)] is t.SP_SUB_THEMES


def test_every_supply_partner_l2_now_has_a_framework():
    """The generalisation of the two above: no SP pair may be left where a
    sub-theme cannot be assigned."""
    missing = [l2 for l2 in t.L2_OPTIONS["Supply Partner Issue"]
               if not has_sub_theme_framework("Supply Partner Issue", l2)]
    assert not missing, f"SP L2s with no framework: {missing}"


# ── 2-4. the rules, and that they reach the model ───────────────────────────

PROMPT = prompts.classification_prompt("x", {}, [])


def test_staff_conduct_is_the_supply_partners_people():
    """The single biggest error class: 9 of 33 L1/L2 misses were venue "staff"
    sent to Venue Related. CX put 11 of 13 such reviews on Guide Behaviour —
    the split was on who employed the person, which the guest cannot see."""
    assert '"STAFF" IS THE SUPPLY PARTNER\'S PEOPLE.' in PROMPT
    for cue in ("lounge staff", "museum staff", "cruise crew", "a driver"):
        assert cue in PROMPT, f"the rule does not name {cue!r}"


def test_the_staff_rule_keeps_conduct_apart_from_conditions():
    """Venue facility issue must survive as the PLACE failing. Widening
    Guide Behaviour to swallow dirt and broken equipment would trade one
    systematic error for another."""
    assert "CONDUCT, NOT CONDITIONS" in PROMPT
    # Fragment, not the whole sentence: the rule is wrapped in the prompt and a
    # match spanning the wrap would break on any reflow, which is a formatting
    # change rather than a lost rule.
    assert "is never a facility" in PROMPT
    assert "dirt, broken equipment, missing signage" in PROMPT


def test_the_staff_rule_still_yields_to_the_primary_complaint():
    """Two of the 13 went to Food and Ticket instead, because that was what
    the review was about. A rule with no exit turns 'rude staff' into a
    keyword that outranks the actual failure."""
    assert "THE PRIMARY COMPLAINT STILL WINS" in PROMPT


def test_an_unreachable_ticket_is_a_ticket_failure():
    assert "A TICKET THE GUEST CANNOT REACH IS A TICKET FAILURE" in PROMPT
    assert "app malfunctioned, causing issues with accessing" in PROMPT


def test_duration_is_timing_and_the_tiebreak_admits_it_is_a_judgement():
    """"Too short / rushed" split 11-7 across two L2s on near-identical
    wording. Stating a bright line there would be inventing a distinction the
    labels do not support, so the rule gives a default and says why."""
    assert "DURATION IS TIMING" in PROMPT
    assert "Short of TIME" in PROMPT and "Short of SUBSTANCE" in PROMPT
    assert "genuine judgement rather than a bright line" in PROMPT


# ── what was deliberately NOT changed ───────────────────────────────────────

def test_no_rule_was_invented_for_the_guide_quality_catchall_split():
    """NEGATIVE, and the most important test here.

    49 reviews were labelled "E. Guide Quality Issue" and 17 "G. Other Supply
    Partner Issue" under the same L2, on wording that does not separate:
    "Guide spoke only English despite booking for a different language" (E) vs
    "Guide only spoke English and French, not Spanish as paid" (G). No rule was
    written for it, because a rule fitted to that would encode annotation noise
    and move the model in an unpredictable direction. If someone later adds one,
    this test should fail and make them justify it against fresh labels.
    """
    assert "E. Guide Quality Issue" not in PROMPT.split("SUB-THEME")[0], (
        "a top-level L1/L2 rule now names the E-vs-G split, which the labelled "
        "sample does not support")
