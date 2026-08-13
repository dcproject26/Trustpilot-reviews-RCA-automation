"""The guest response goes out in the review's language, from ONE store.

The defect this replaces: the reply was stored in ENGLISH, the guest's
language existed only as `state.replyTranslation` in the browser — memory that
did not survive a reload — and Send/Copy read the English. A card labelled the
translated text "what the guest receives" while the thing that actually went
out was the English above it.

So the guarantees are about which field is the truth, and about a failure
never leaving the two halves disagreeing without saying so.
"""
import asyncio
from types import SimpleNamespace

import pytest

from server.services.reply_language import (english_view, digest, is_english,
                                            language_state, outgoing,
                                            set_english_projection,
                                            translate_outgoing)


def _review(language="IT"):
    """A review whose BODY matches its declared language.

    "English" — the NAME — is what the detector writes, and is the only value
    that means the review was established as English. The two-letter "en" is
    the ingest default `slack.parse_review` used to stamp on everything, and
    means NOBODY LOOKED; a review carrying it draws two boxes.

    An English review whose body_original is Italian and body_english is not
    would be a review that was translated on the way in, which
    `language_state` correctly refuses to call English — see
    test_a_review_filed_as_en_but_translated_inbound_is_not_english.
    """
    if (language or "").lower() in ("en", "english"):
        return SimpleNamespace(id="tp_1", language=language, rating=2,
                               body_original="They never arrived",
                               body_english="They never arrived")
    return SimpleNamespace(id="tp_1", language=language, rating=2,
                           body_original="Non sono mai arrivati",
                           body_english="They never arrived")


def _draft(**kw):
    base = dict(suggested_response="", final_response="",
                response_english=None, response_english_of=None)
    base.update(kw)
    return SimpleNamespace(**base)


# ── One store: what actually goes to the guest ──────────────────────────────

def test_the_outgoing_reply_is_the_human_edit_over_the_machine_draft():
    assert outgoing(_draft(suggested_response="macchina")) == "macchina"
    assert outgoing(_draft(suggested_response="macchina",
                           final_response="umano")) == "umano"


def test_the_english_projection_is_never_the_outgoing_reply():
    """The English box is a working view. Nothing that sends, copies or posts
    may read it — so `outgoing()` must ignore it entirely, even when it is the
    only text present."""
    d = _draft(response_english="They never arrived")
    assert outgoing(d) == ""


def test_outgoing_survives_a_missing_draft():
    assert outgoing(None) == ""


# ── Which of the three cases a review is in ─────────────────────────────────

def test_an_english_review_is_one_box_and_no_translation():
    st = language_state(_review("English"))
    assert st["state"] == "english"
    v = english_view(_review("English"), _draft(final_response="Sorry about that"))
    assert v["state"] == "same"
    assert v["text"] == "Sorry about that"
    assert v["text"] == v["outgoing"]


def test_a_missing_language_is_not_treated_as_english():
    """An empty language is NOT English. Assuming it would send an English
    reply to a guest who may not read it, silently — the inbound translation
    is what populates the field, so a blank means that step did not run."""
    assert not is_english(_review(""))
    st = language_state(_review(""))
    assert st["state"] == "unknown"
    assert st["state"] != "english"
    assert "translated into English on the way in" in st["why"]


def test_a_non_english_review_reports_its_language():
    st = language_state(_review("IT"))
    assert st["state"] == "translated"
    assert st["language"] == "IT"


# ── The English view says how far it can be trusted ─────────────────────────

def test_a_projection_matching_the_outgoing_reply_is_current():
    out = "Non sono mai arrivati"
    d = _draft(final_response=out, response_english="They never arrived",
               response_english_of=digest(out))
    v = english_view(_review("IT"), d)
    assert v["state"] == "current"


def test_editing_the_outgoing_reply_directly_makes_the_english_stale():
    """A direct edit to the guest-language box is the associate's own words
    and must survive. The English beside it is then from before that edit, and
    showing it as current would present a superseded translation as the reply
    about to go out."""
    d = _draft(final_response="Non sono mai arrivati",
               response_english="They never arrived",
               response_english_of=digest("qualcosa di completamente diverso"))
    v = english_view(_review("IT"), d)
    assert v["state"] == "stale"
    assert "edited directly" in v["why"]


def test_no_projection_yet_is_absent_not_stale():
    """Never made and gone behind are different facts. Reporting a missing
    projection as stale would send someone looking for an edit nobody made."""
    v = english_view(_review("IT"), _draft(final_response="Non sono mai arrivati"))
    assert v["state"] == "absent"


def test_setting_the_projection_writes_both_halves_together():
    d = _draft(final_response="old")
    set_english_projection(d, "They never arrived", "Non sono mai arrivati")
    assert d.final_response == "Non sono mai arrivati"
    assert d.response_english == "They never arrived"
    assert english_view(_review("IT"), d)["state"] == "current"


# ── The translation on the way in ───────────────────────────────────────────

class _FakeClaude:
    def __init__(self, result=None, boom=False):
        self.result, self.boom, self.calls = result, boom, 0

    async def translate_to(self, text, lang, review_id=None):
        self.calls += 1
        if self.boom:
            raise RuntimeError("upstream 502")
        return self.result


@pytest.fixture
def fake_claude(monkeypatch):
    def _install(result=None, boom=False):
        fake = _FakeClaude(result, boom)
        import server.services.claude as real
        monkeypatch.setattr(real, "translate_to", fake.translate_to)
        return fake
    return _install


def test_a_non_english_review_gets_a_translated_outgoing_reply(fake_claude):
    fake_claude("Non sono mai arrivati")
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "They never arrived", _review("IT"), "tp_1"))
    assert out == "Non sono mai arrivati"
    assert eng == "They never arrived"
    assert of == digest(out)
    assert trail["mark"] == "pass"
    assert "translated to IT" in trail["text"]


def test_an_english_review_is_not_translated_and_says_so(fake_claude):
    """ONE box. No translation call is made at all, and the trail says the
    review was English rather than leaving a reader to infer it."""
    fake = fake_claude("should not be used")
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "Sorry about that", _review("English"), "tp_1"))
    assert out == "Sorry about that"
    assert fake.calls == 0
    assert eng == ""          # no projection: there is nothing to project
    assert of is None
    assert trail["mark"] == "pass"
    assert "nothing was translated" in trail["text"]


def test_a_failed_translation_leaves_english_and_says_it_is_english(fake_claude):
    """The reply is ENGLISH on a review that is not, and the trail says so.
    Silence here would leave a card identical to a successful translation, and
    Send would put English on a review page written in Italian."""
    fake_claude(boom=True)
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "They never arrived", _review("IT"), "tp_1"))
    assert out == "They never arrived"
    assert trail["mark"] == "warn"
    assert "still ENGLISH" in trail["text"]


def test_a_failed_translation_leaves_no_english_projection(fake_claude):
    """No projection, because the outgoing text IS the English. Storing one
    would draw two boxes claiming a translation that never happened."""
    fake_claude(result="")
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "They never arrived", _review("IT"), "tp_1"))
    assert eng == ""
    assert of is None
    assert trail["mark"] == "warn"


def test_a_review_with_no_language_warns_rather_than_assuming_english(fake_claude):
    fake = fake_claude("unused")
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "They never arrived", _review(""), "tp_1"))
    assert out == "They never arrived"
    assert fake.calls == 0
    assert trail["mark"] == "warn"
    assert "no language is recorded" in trail["text"]


def test_a_blank_reply_produces_no_language_warning(fake_claude):
    """A deliberately blank reply (prompt rule 20) is not a language failure,
    and warning about one would put a red line on a card whose reply was
    correctly left empty."""
    fake = fake_claude("unused")
    out, eng, of, trail = asyncio.run(translate_outgoing("", _review("IT"), "tp_1"))
    assert out == ""
    assert trail is None
    assert fake.calls == 0


# ── The `en` default that is not a finding ─────────────────────────────────

def _translated_in(language="en"):
    """A review filed as English whose text was demonstrably translated."""
    return SimpleNamespace(id="tp_2", language=language,
                           body_original="Non sono mai arrivati",
                           body_english="They never arrived")


def test_a_review_filed_as_en_but_translated_inbound_is_not_english():
    """`slack.parse_review()` hard-codes `language: "en"` on every ingested
    review and nothing updates it — the inbound translation writes
    `body_english` and leaves the column alone. So `en` beside a body that was
    translated is the DEFAULT showing through, not a finding, and trusting it
    would send an English reply to a guest who did not write in English.
    """
    st = language_state(_translated_in())
    assert st["state"] == "unknown", (
        "a review whose text was translated on the way in is being treated as "
        "English because of a column default")
    assert not is_english(_translated_in())
    assert "translated into English on the way in" in st["why"]


def test_a_detected_english_is_overruled_by_a_translation_that_happened():
    """THE CONTRADICTION, resolved towards two boxes. The column says English
    — a NAME, so something detected it — and the review's own text says
    otherwise: body_english exists and DIFFERS, which only happens when the
    inbound model actually translated something (it answers ENGLISH_ALREADY
    for English and writes nothing).

    Detection is a model call and can be wrong. A translation that
    demonstrably happened is a record. The record wins, because the cost of
    believing it wrongly is one spare box and the cost of believing the column
    wrongly is an English reply to a guest who did not write in English."""
    rv = SimpleNamespace(id="tp_c", language="English",
                         body_original="Non sono mai arrivati",
                         body_english="They never arrived")
    st = language_state(rv)
    assert st["state"] == "unknown", st
    assert "translated on the way in" in st["why"]
    assert not is_english(rv)


def test_the_two_english_decisions_cannot_disagree():
    """`is_english()` and `language_state()` must not each decide it their own
    way — one box on the card beside a send path that believes it owes a
    translation is a disagreement neither side would report."""
    for rv in (_review("English"), _review("IT"), _review(""), _translated_in()):
        assert is_english(rv) == (language_state(rv)["state"] == "english")


def test_a_genuinely_english_review_is_still_one_box():
    """The inverse bug: body_english equal to body_original (or absent) is a
    review that really is English, and must not be dragged into `unknown`."""
    same = SimpleNamespace(id="tp_3", language="English",
                           body_original="They never arrived",
                           body_english="They never arrived")
    assert language_state(same)["state"] == "english"
    none_yet = SimpleNamespace(id="tp_4", language="English",
                               body_original="They never arrived",
                               body_english=None)
    assert language_state(none_yet)["state"] == "english"


def test_the_ingest_default_is_decided_by_whether_it_was_translated():
    """`en` is what `parse_review` stamped on EVERY review, so it cannot be
    read as "English" on its own — but it must not force two boxes onto every
    review either, or every genuinely English review grows a translate panel
    it does not need. The free signal decides it: `body_english` is written
    only when the inbound step actually translated something.

    A Spanish review filed as `en` (body_english differs) is NOT English and
    draws two boxes. A review nothing translated IS English and draws one.
    This is the responses-are-broken regression, both directions."""
    spanish = SimpleNamespace(id="tp_es", language="en",
                              body_original="No devuelven el dinero.",
                              body_english="They don't refund the money.")
    assert language_state(spanish)["state"] == "unknown", "a Spanish review got one box"

    for body_en in (None, "", "They never arrived"):
        english = SimpleNamespace(id="tp_en", language="en",
                                  body_original="They never arrived",
                                  body_english=body_en)
        assert language_state(english)["state"] == "english", (
            f"a genuinely English review got two boxes (body_english={body_en!r})")


def test_a_recorded_non_english_language_still_wins():
    """A real language code is a finding and is used, translated inbound or
    not — the default-detection above must not override it."""
    rv = SimpleNamespace(id="tp_5", language="IT",
                         body_original="Non sono mai arrivati",
                         body_english="They never arrived")
    st = language_state(rv)
    assert st["state"] == "translated" and st["language"] == "IT"


def test_the_defaulted_case_refuses_rather_than_translating_to_nothing(fake_claude):
    fake = fake_claude("unused")
    out, eng, of, trail = asyncio.run(translate_outgoing(
        "They never arrived", _translated_in(), "tp_2"))
    assert fake.calls == 0, "it tried to translate into a language it does not know"
    assert out == "They never arrived"
    assert trail["mark"] == "warn"


# ── The guest's language is READ, never typed ───────────────────────────────
#
# The card used to show a "Guest's language — e.g. Spanish" text box, and the
# reason it had to was a gate on the wrong thing: detection ran inside
# `if not review.body_english:`, so it only fired when the inbound translation
# happened on THAT run. A review translated earlier — or simply re-run — kept
# the "en" that parse_review hard-codes, `language_state` said "unknown", and
# an associate was asked to identify a language whose source text we are
# holding in `body_original`.

class _FakeDetector:
    """Stands in for the detection call, and RECORDS whether it was made.

    "we could not name the language" and "we never asked" produce the same
    card, so the tests below assert on `calls` and not only on the outcome.
    """
    def __init__(self, answer=""):
        self.answer, self.calls = answer, []

    async def detect_language(self, text):
        self.calls.append(text)
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


@pytest.fixture
def fake_detector(monkeypatch):
    def _install(answer=""):
        import server.services.claude as real
        fc = _FakeDetector(answer)
        monkeypatch.setattr(real, "detect_language", fc.detect_language)
        return fc
    return _install


def _resolve(review):
    from server.services.reply_language import resolve_language
    return asyncio.run(resolve_language(review))


def test_a_cached_translation_no_longer_blocks_detection(fake_detector):
    """THE BUG, stated directly. This review's `body_english` is already
    present — the state every re-run and every pre-existing review is in — and
    that used to mean the detection branch never opened at all."""
    fc = fake_detector("French")
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="Ils ne sont jamais arrivés",
                        body_english="They never arrived")
    res = _resolve(r)
    assert fc.calls, "the detection was never called — the old gate is back"
    assert res["outcome"] == "detected"
    assert res["language"] == "French"
    assert r.language == "French", "the detection ran and nothing recorded it"


def test_the_detected_language_makes_the_card_draw_two_boxes():
    """The point of recording it. Driven through `language_state`, which is
    what the card and the send path both read."""
    r = SimpleNamespace(id="tp_1", language="French",
                        body_original="Ils ne sont jamais arrivés",
                        body_english="They never arrived")
    st = language_state(r)
    assert st["state"] == "translated" and st["language"] == "French"


def test_an_undetectable_language_records_nothing_and_says_it_looked(
        fake_detector, monkeypatch):
    """A guess here sends the guest a reply they cannot read. The column is
    left alone — and the outcome is NOT the same word as "we did not run"."""
    fc = fake_detector("")
    import server.config as _cfg
    monkeypatch.setattr(_cfg, "is_live", lambda name: name == "anthropic")
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="....",
                        body_english="They never arrived")
    res = _resolve(r)
    assert fc.calls, "nothing was asked"
    assert res["outcome"] == "undetected"
    assert r.language == "en", "a language was invented"
    assert "could not be named" in res["why"], res["why"]


def test_a_failed_detection_call_is_not_reported_as_english(fake_detector):
    fake_detector(RuntimeError("upstream down"))
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="Ils ne sont jamais arrivés",
                        body_english="They never arrived")
    res = _resolve(r)
    assert res["outcome"] == "failed", (
        "a crashed lookup is reported as though it ran and found nothing")
    assert r.language == "en"
    assert "failed" in res["why"]


def test_a_detector_that_is_not_connected_says_so_rather_than_undetected():
    """"Switched off on this server" and "read it and could not place it" are
    different problems with different next steps — one is escalated, the other
    is this review being hard. `detect_language` returns "" for both."""
    import server.config as _cfg
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="Ils ne sont jamais arrivés",
                        body_english=None)
    assert not _cfg.is_live("anthropic"), "this test needs Anthropic offline"
    res = _resolve(r)
    assert res["outcome"] == "unavailable", res
    assert "not connected" in res["why"]
    assert r.language == "en", "a language was invented with nothing to read it from"


def test_an_english_review_is_asked_once_and_then_left_alone(fake_detector):
    """IT IS ASKED, and that is the change. The old rule read
    `body_original == body_english` as positive evidence of English, but that
    is also what an empty body_english looks like after a crashed translate —
    so it declared English on a failed lookup.

    The cost of asking is ONE detect call per review, not one per render:
    the answer is stored as a NAME and `skipped_known` covers it ever after."""
    fc = fake_detector("English")
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="They never arrived",
                        body_english="They never arrived")
    res = _resolve(r)
    assert res["outcome"] == "detected"
    assert r.language == "English"
    assert len(fc.calls) == 1

    again = _resolve(r)
    assert again["outcome"] == "skipped_known", "it was asked a second time"
    assert len(fc.calls) == 1, "a stored language was sent for detection again"


def test_a_review_that_already_names_its_language_is_left_alone(fake_detector):
    """Re-detecting would overwrite a language somebody may have corrected by
    hand, and it is a model call for a question already answered."""
    fc = fake_detector("Spanish")
    r = SimpleNamespace(id="tp_1", language="Italian",
                        body_original="Non sono mai arrivati",
                        body_english="They never arrived")
    res = _resolve(r)
    assert res["outcome"] == "skipped_known"
    assert not fc.calls
    assert r.language == "Italian"


def test_a_language_CODE_is_refused_rather_than_stored(fake_detector):
    """A LOOP THAT REPORTS SUCCESS, found by code review.

    `detect_language` filters UNKNOWN, blanks, over-long answers and anything
    with a space — but a bare two-letter code passes all four. Stored, the
    outcome says `detected` and the column looks filled, while
    `language_state` reads it straight back as UNESTABLISHED, because that is
    exactly what `en` means. The card then fires the language check again on
    every render, spending a model call each time and never settling.

    Storing nothing is strictly better: the card still shows two boxes, and it
    stops asking a detector that cannot answer usefully."""
    fc = fake_detector("en")
    r = SimpleNamespace(id="tp_1", language="en",
                        body_original="Non sono mai arrivati",
                        body_english=None)
    res = _resolve(r)
    assert res["outcome"] == "undetected", res
    assert r.language == "en", "a code was written into the column"
    assert "code" in res["why"], res["why"]
    assert len(fc.calls) == 1


def test_a_real_language_name_is_still_stored(fake_detector):
    """Paired, so the guard cannot swallow good answers."""
    fc = fake_detector("Italian")
    r = SimpleNamespace(id="tp_2", language="en",
                        body_original="Non sono mai arrivati",
                        body_english=None)
    res = _resolve(r)
    assert res["outcome"] == "detected"
    assert r.language == "Italian"
    assert language_state(r)["state"] == "translated"
