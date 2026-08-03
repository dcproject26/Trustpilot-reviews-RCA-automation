"""The naming rules the prompt states are the rules the code enforces.

Two rules, both of which cost a match on a real review:

  1. salutations are not names — "Mrs Fredrik Olsen" matches no Zendesk
     requester;
  2. the middle name is not optional — "Bhayani Salim F" must be searched as
     "Bhayani Salim", not "Bhayani F".

They are written down twice: as instructions to the model in
match_indicator_prompt, and as code in server/names.py, because the model can
ignore an instruction and the code cannot. Two statements of one rule is
exactly the arrangement that drifts, so every salutation the prompt names is
checked against what the code actually strips.

A prompt that lists "Miss" while the code keeps it is worse than neither: the
model complies, the code re-adds it, and the search fails for a reason nobody
can see from either file alone.
"""
import re

import pytest

from server.names import name_tokens, parse_author, search_tokens
from server.prompts import match_indicator_prompt


def _prompt():
    return match_indicator_prompt("a review body", "2026-08-01",
                                  reviewer_name="Bhayani Salim F")


def _guest_name_section() -> str:
    t = _prompt()
    start = t.index("- guest_name")
    end = t.index("- experience_or_venue", start)
    return t[start:end]


# ── the rules are actually in the prompt ────────────────────────────────────

def test_the_prompt_tells_the_model_to_drop_salutations():
    s = _guest_name_section().lower()
    assert "salutation" in s
    for word in ("mr", "mrs", "miss", "dr"):
        assert f'"{word}"' in s.lower() or f"'{word}'" in s.lower(), \
            f"{word!r} is not named as a salutation to drop"


def test_the_prompt_tells_the_model_to_keep_the_middle_name():
    s = _guest_name_section()
    assert "MIDDLE" in s.upper()
    assert "Bhayani Salim" in s, "the worked example is missing"
    assert "NOT \"Bhayani F\"" in s or "NOT 'Bhayani F'" in s, \
        "the prompt does not say what the WRONG answer looks like"


def test_the_prompt_says_an_initial_is_not_a_name():
    s = _guest_name_section().lower()
    assert "initial" in s
    assert "two letters or more" in s, \
        "without this the model may also drop Li, Bo and Ng"


# ── and the code enforces every one of them ─────────────────────────────────

def _salutations_named_in_the_prompt():
    """The quoted words in the drop-salutations rule, read off the prompt.

    Read rather than listed, so a word added to the prompt and not to the code
    fails here rather than in production.

    Returns [] rather than raising when the rule is not there. This is called
    at import time to parametrize, so an exception here is a COLLECTION error:
    the whole file fails with a ValueError traceback and never says which rule
    went missing. Removing the salutation rule from the prompt did exactly
    that — the mutation was caught, but by a crash that names nothing.
    Degrading to [] lets the assertion below do the talking.
    """
    s = _guest_name_section()
    try:
        rule = s[s.index("DROP SALUTATIONS"):s.index("KEEP EVERY NAME TOKEN")]
    except ValueError:
        return []
    return re.findall(r'"([A-Za-z]{2,5})"', rule)


def test_the_salutation_list_was_found_at_all():
    """A test that silently found nothing to check is the failure this suite
    is about — and the parametrized test below would report PASSED with zero
    cases if the rule vanished, which reads exactly like it verified something.
    """
    got = _salutations_named_in_the_prompt()
    assert got, ("the prompt's DROP SALUTATIONS rule is gone, so nothing tells "
                 "the model to strip Mr/Mrs/Miss — and the per-salutation "
                 "checks below just ran zero times")
    assert len(got) >= 6, f"only found {got} in the prompt's salutation rule"


@pytest.mark.parametrize("salutation", _salutations_named_in_the_prompt())
def test_every_salutation_the_prompt_names_is_stripped_by_the_code(salutation):
    """The model may leave it in. The code must take it out anyway."""
    toks = name_tokens(f"{salutation} Fredrik Olsen")
    assert salutation not in toks, (
        f"the prompt says to drop {salutation!r} and server/names.py keeps it, "
        f"so a compliant model and a non-compliant one give different results")
    assert parse_author(f"{salutation} Fredrik Olsen") == ("Fredrik", "Olsen")


def test_the_code_keeps_the_middle_name_the_prompt_promises():
    assert parse_author("Bhayani Salim F") == ("Bhayani", "Salim")
    assert search_tokens("Bhayani Salim F") == ["Bhayani", "Salim"]


def _two_letter_names_in_the_prompt():
    """The example names in the keep-two-letter-names rule, read off the prompt.

    Listing them here instead would let the prompt lose its examples silently —
    which mutation testing demonstrated: deleting `"Bo" and "Ng" are real
    names` from the prompt killed nothing, because the test only checked that
    the phrase "two letters or more" was still there. A rule with no example
    is a rule the model is much more likely to ignore, and the failure lands
    on one demographic of guest names and nobody else's.
    """
    s = _guest_name_section()
    try:
        rule = s[s.index("A bare INITIAL"):]
    except ValueError:
        return []
    return [n for n in re.findall(r'"([A-Za-z]{2})"', rule)]


def test_the_two_letter_rule_still_has_examples():
    got = _two_letter_names_in_the_prompt()
    assert len(got) >= 2, (
        "the keep-two-letter-names rule no longer names any — a bare rule is "
        f"much easier for the model to ignore. Found: {got}")


@pytest.mark.parametrize("name", _two_letter_names_in_the_prompt() or ["Li"])
def test_the_code_keeps_two_letter_names_the_prompt_promises(name):
    assert name in search_tokens(f"{name} Andersson"), \
        f"{name!r} was dropped as an initial; the prompt promises otherwise"
    assert parse_author(f"Fredrik {name}") == ("Fredrik", name), \
        f"{name!r} is not accepted as a surname"


def test_the_code_drops_the_bare_initial_the_prompt_promises():
    assert "F" not in search_tokens("Bhayani Salim F")
    assert "F" not in search_tokens("Bhayani Salim F.")


# ── the field description matches what the matcher does with it ─────────────

def test_the_prompt_says_every_token_is_searched():
    """The consumer note at the foot of the prompt. If it claims something the
    matcher does not do, the model is being told to optimise for the wrong
    thing."""
    t = _prompt()
    tail = t[t.index("Every field above is consumed by the matcher"):]
    assert "EVERY token you return is searched" in tail
