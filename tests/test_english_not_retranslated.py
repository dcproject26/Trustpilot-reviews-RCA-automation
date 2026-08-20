"""An English review must never be handed to a branch that will paraphrase it.

THE BUG. `translation_prompt`'s explicit-language branch was an unconditional
"translate into clear English" with no ENGLISH_ALREADY escape. English reviews
legitimately keep an empty body_english, so the inbound translate step re-enters
on every later pipeline run — and by then `resolve_language` has overwritten the
review's language with the detector's full name, "English". "English" is not in
("en", "auto", ""), so the review took the unconditional branch and was
translated English -> English, then stored and shown as an "English translation".

The guard downstream (english_or_reject) cannot catch this: a paraphrased English
result IS English, so it validates and stores. The only place to stop it is here,
by making every branch able to answer ENGLISH_ALREADY.

Driven through translation_prompt() itself — the routing IS the logic, and the
input is the untrustworthy `lang` label in each of the forms this system stores.
"""
import pytest

from server.prompts import translation_prompt, _denotes_english

BODY = "Straightforward English review, the tour was late and nobody told us."


@pytest.mark.parametrize("lang", ["English", "english", "en", "en-US", "en_GB",
                                  "eng", "EN", ""])
def test_every_english_label_can_answer_english_already(lang):
    """THE POINT. Whatever form the stored language takes, the prompt built for
    it must let the model say ENGLISH_ALREADY — otherwise an English review is
    paraphrased into English."""
    p = translation_prompt(BODY, lang)
    assert "ENGLISH_ALREADY" in p, (
        f"lang={lang!r} produced a prompt with no English-already escape, so "
        f"an English review would be translated into English")


def test_the_full_name_english_is_not_treated_as_a_foreign_language():
    """The exact regression: language stored as "English" by resolve_language.
    It must reach the DETECT branch, not the explicit-translate branch."""
    p = translation_prompt(BODY, "English")
    assert "Detect the language" in p
    assert "Original (English):" not in p, (
        "an English review was routed to the explicit-translate branch, which "
        "is what paraphrased it")


def test_a_real_foreign_language_still_gets_the_translate_branch():
    """The fix must not disable translation. A Spanish review still goes to the
    explicit branch, keeps its language hint — and now ALSO carries the escape,
    because even a 'Spanish' label can be wrong."""
    p = translation_prompt("Reserva cancelada sin avisar.", "Spanish")
    assert "Original (Spanish):" in p
    assert "Translate this Trustpilot review" in p
    assert "ENGLISH_ALREADY" in p


def test_denotes_english_covers_the_forms_and_rejects_others():
    for yes in ("en", "EN", "en-US", "en_gb", "English", "english", "eng"):
        assert _denotes_english(yes), yes
    for no in ("es", "Spanish", "fr", "", "auto", "de-DE", "enigma"):
        assert not _denotes_english(no), no


def test_enigma_is_not_english_despite_the_prefix():
    """A word that merely starts with 'en' is not a locale. `startswith("en-")`
    guards the locale form; a bare 'enigma' must fall through to translation."""
    assert not _denotes_english("enigma")
    p = translation_prompt("Enigma text in another language.", "enigma")
    assert "Original (enigma):" in p
