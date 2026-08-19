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
    rules = PROMPT.split("--- Sub-theme framework")[0]
    # The guard is on the PAIR, not on either label alone. Naming E is fine and
    # now happens for a different reason — "use I, not E, when the booked tour
    # was not delivered" is a rule the labels DO support. What must not appear
    # is E and G set against each other, because nothing separates them.
    assert not ("E. Guide Quality Issue" in rules
                and "G. Other Supply Partner Issue" in rules), (
        "a top-level rule now contrasts E with G, which the labelled sample "
        "does not support — 49 vs 17 on wording that does not separate")


# ── a sub-theme created because the labels had nowhere to put something ─────

def test_the_booked_tour_not_provided_has_its_own_sub_theme():
    """The booking names the variant sold — bookings_tour_name reads "Spanish
    Guided Tour", "French Guided Tour" — so a guest who bought one and got
    another did not receive the PRODUCT. Not poor guiding: the guide may have
    guided perfectly. With nothing covering it, 11 such reviews in a labelled
    sample scattered across E, D and G.

    Named for what the review says rather than for language, which is the
    commonest form of it and not the whole of it.
    """
    names = [n for _c, n, _cu in t.SP_SUB_THEMES["sub_themes"]]
    assert "Booked Tour Not Provided" in names


def test_the_new_theme_did_not_take_an_letter_already_in_use():
    """THE POINT OF THE ODD LETTER, and the reason it is not G.

    classifier._salvage_sub_theme matches on the letter PREFIX. Renaming G would
    silently convert every stored "G. Other Supply Partner Issue" into the new
    theme — a wrong answer with no warning, on data already labelled. Letters
    here are append-only; the LIST order carries the reading order instead.
    """
    by_code = {c: n for c, n, _ in t.SP_SUB_THEMES["sub_themes"]}
    assert by_code["G"] == "Other Supply Partner Issue"
    assert t.SP_SUB_THEMES["exclusion_label"] == "H. Irrelevant"
    assert by_code["I"] == "Booked Tour Not Provided"


def test_salvage_still_maps_the_old_letters_to_the_old_themes():
    """The guarantee the letter choice exists to protect, driven rather than
    asserted about the data structure."""
    from server.services.classifier import _salvage_sub_theme
    GQ = "Guide providing irrelevant/inexperienced/not clear"
    assert _salvage_sub_theme("Supply Partner Issue", GQ, "G") == "G. Other Supply Partner Issue"
    assert _salvage_sub_theme("Supply Partner Issue", GQ, "H") == "H. Irrelevant"


def test_salvage_reaches_past_h():
    """It was `[a-h]`, a hardcoded alphabet that silently stopped salvaging the
    moment a framework grew — which is this commit. The range is derived from
    the framework now."""
    from server.services.classifier import _salvage_sub_theme
    GQ = "Guide providing irrelevant/inexperienced/not clear"
    assert _salvage_sub_theme("Supply Partner Issue", GQ, "I") == "I. Booked Tour Not Provided"


def test_the_new_theme_is_valid_on_every_sp_l2():
    from server.taxonomy import is_valid_sub_theme
    for l2 in t.L2_OPTIONS["Supply Partner Issue"]:
        assert is_valid_sub_theme("Supply Partner Issue", l2,
                                  "I. Booked Tour Not Provided"), l2


def test_an_audio_guide_language_problem_is_not_this_theme():
    """5 of the 11 language complaints in the sample were about the AUDIO guide,
    which already has D. AG Language Issues. Without this boundary the new theme
    swallows the more common half."""
    assert "A HUMAN GUIDE, NOT AN AUDIO GUIDE" in PROMPT
    assert "D. AG Language Issues" in PROMPT


def test_the_framework_declares_all_eight_sp_l2s():
    """`applies_to_l2` is printed INTO the prompt to say where the framework
    holds. It listed six, so the model was being told two of its own L2s were
    out of scope for the themes it was being handed."""
    assert set(t.SP_SUB_THEMES["applies_to_l2"]) == set(t.L2_OPTIONS["Supply Partner Issue"])


def test_not_provided_audio_guides_have_one_home():
    """"no audio guide provided", "was not provided" and "app not provided as
    expected" landed on G, A and F in the sample — the same complaint in three
    places because the tiebreak spoke only about obtaining and failing."""
    assert t.AG_SUB_THEMES["tiebreak_rule"].startswith("NOT PROVIDED")
    assert "= A, always" in t.AG_SUB_THEMES["tiebreak_rule"]


def test_the_theme_is_not_named_for_language_alone():
    """It began as a language theme and that was too narrow. Language is how
    this failure usually presents; the failure is the booked variant not being
    delivered, and a name that says "language" would send group-size and
    private-vs-shared cases back to the catchall."""
    names = [n for _c, n, _cu in t.SP_SUB_THEMES["sub_themes"]]
    assert not [n for n in names if "Language" in n], names
    cues = dict((c, cu) for c, _n, cu in t.SP_SUB_THEMES["sub_themes"])["I"]
    assert any("private tour" in c for c in cues), (
        "the cues cover only language, so the widened name is not backed by "
        "anything the model can match on")


def test_the_model_is_actually_told_to_use_the_new_sub_theme():
    """THE WIRING, and mutation testing is why it is here.

    Every other test on this theme checked the TAXONOMY — that it exists, has
    the right letter, validates on each L2. All of them passed against a build
    with the rule deleted from the prompt, because a sub-theme the model is
    never told about is one it will never emit. The framework listing alone
    gives a name and no instruction to prefer it over E, which is where these
    reviews were going before.
    """
    assert "THE GUEST GOT A DIFFERENT TOUR FROM THE ONE THEY BOOKED" in PROMPT
    assert "I. Booked Tour Not Provided" in PROMPT
    assert 'NOT "E. Guide Quality Issue"' in PROMPT, (
        "the rule names the new theme without saying what it displaces, which "
        "is the whole correction")
    assert "READ WHAT THE REVIEW IS SAYING" in PROMPT
