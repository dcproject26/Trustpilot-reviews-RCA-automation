"""A review whose inbound translation never ran must not be called English.

`body_english` empty means one of two things that write the same bytes: the
inbound step answered ENGLISH_ALREADY (the review IS English), or it never ran
/ crashed (the review may be anything). Reading both as English sent an English
reply to a guest who did not write in English.

The guard is POSITIVE EVIDENCE ONLY — it moves to two boxes when the guest's
own words carry something English does not do, never on the absence of English
words. The second half of this file is the reason: real English reviews often
carry no function word at all, and flagging those is the regression that
`912e03c` was written to undo.
"""
from types import SimpleNamespace as NS

from server.services.reply_language import language_state, _looks_non_english


def _state(language, orig, eng=None):
    return language_state(NS(id="tp_t", language=language,
                             body_original=orig, body_english=eng))["state"]


# ── the hole: a non-English review whose translation did not run ───────────

def test_the_ingest_default_on_untranslated_italian_is_two_boxes():
    assert _state("en", "Non sono mai arrivati") == "unknown"


def test_the_new_ingest_none_on_untranslated_italian_is_two_boxes():
    assert _state(None, "Non sono mai arrivati") == "unknown"


def test_a_non_latin_script_is_never_called_english():
    assert _state("en", "Билеты так и не пришли") == "unknown"
    assert _state("en", "チケットが届きませんでした") == "unknown"


def test_the_reason_says_the_translation_did_not_run():
    # Not "we found English" — the distinction this module exists to keep.
    why = language_state(NS(id="t", language="en",
                            body_original="Non sono mai arrivati",
                            body_english=None))["why"]
    assert "did not run" in why


# ── what must NOT change: genuine English keeps ONE box ────────────────────

def test_english_without_any_function_word_stays_one_box():
    # THE REGRESSION THIS GUARD MUST NOT REINTRODUCE. Asking "does this look
    # English?" would flag all of these, and a translate panel on a plain
    # English review is what `912e03c` was written to remove.
    for text in ("Great tour guide",
                 "Terrible experience overall",
                 "Awful. Waste of money"):
        assert _state("en", text) == "english", text


def test_english_reviews_naming_foreign_places_stay_one_box():
    # "los"/"las"/"del" would have flagged these; they are deliberately absent
    # from the marker set.
    assert _state("en", "Los Angeles pickup was late") == "english"


def test_english_abbreviations_that_collide_with_foreign_words_stay_one_box():
    # "est" (EST the timezone) and "mit" (MIT) are excluded for exactly this.
    assert _state("en", "Booked for 9am EST, no show") == "english"
    assert _state("en", "MIT campus tour was good") == "english"


def test_a_guest_named_les_does_not_make_the_review_french():
    assert _state("en", "Les was a great guide") == "english"


def test_a_very_short_review_is_not_judged_on_one_word():
    # Two words is too little to read a language off, and "Mai" is a name as
    # often as it is Italian. No claim: the existing behaviour stands.
    assert _looks_non_english("Mai") is False
    assert _looks_non_english("") is False


# ── the paths that already worked are untouched ────────────────────────────

def test_a_successful_inbound_translation_still_gives_two_boxes():
    assert _state("en", "Non sono mai arrivati", "They never arrived") == "unknown"


def test_a_detected_english_review_is_still_one_box():
    assert _state("English", "They never arrived", "They never arrived") == "english"


def test_a_detected_language_is_still_translated():
    assert _state("Italian", "Non sono mai arrivati") == "translated"
