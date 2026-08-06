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

    An "EN" review whose body_original is Italian and body_english is not
    would be a review that was translated on the way in, which
    `language_state` correctly refuses to call English — see
    test_a_review_filed_as_en_but_translated_inbound_is_not_english.
    """
    if (language or "").upper() == "EN":
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
    st = language_state(_review("EN"))
    assert st["state"] == "english"
    v = english_view(_review("EN"), _draft(final_response="Sorry about that"))
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
    assert "no language was recorded" in st["why"]


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
        "Sorry about that", _review("EN"), "tp_1"))
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
    assert "translated on the way in" in st["why"]


def test_the_two_english_decisions_cannot_disagree():
    """`is_english()` and `language_state()` must not each decide it their own
    way — one box on the card beside a send path that believes it owes a
    translation is a disagreement neither side would report."""
    for rv in (_review("EN"), _review("IT"), _review(""), _translated_in()):
        assert is_english(rv) == (language_state(rv)["state"] == "english")


def test_a_genuinely_english_review_is_still_one_box():
    """The inverse bug: body_english equal to body_original (or absent) is a
    review that really is English, and must not be dragged into `unknown`."""
    same = SimpleNamespace(id="tp_3", language="en",
                           body_original="They never arrived",
                           body_english="They never arrived")
    assert language_state(same)["state"] == "english"
    none_yet = SimpleNamespace(id="tp_4", language="en",
                               body_original="They never arrived",
                               body_english=None)
    assert language_state(none_yet)["state"] == "english"


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
