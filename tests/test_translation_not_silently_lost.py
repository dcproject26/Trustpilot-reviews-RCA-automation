"""Foreign-language translations must not be silently lost.

Three causes, each driven through real functions with the model calls mocked:
  1. detect_language threw away multi-word names ("Brazilian Portuguese").
  2. A verifier failure discarded a good translation.
  3. No durable record (translation_state) of refuse/fallback (CLAUDE.md rule 1).
"""
import asyncio
from types import SimpleNamespace

import pytest

from server.services import claude, reply_language as RL

GOOD = "I booked the tickets and we were very happy with the tour guide."


def _aret(v):
    async def _f(*a, **k):
        return v
    return _f


def _araise(exc):
    async def _f(*a, **k):
        raise exc
    return _f


def _review(lang="Portuguese", state=None, english=None):
    return SimpleNamespace(body_original="Reservei os bilhetes.", language=lang,
                           body_english=english, translation_state=state)


# -- cause 1: multi-word language names -------------------------------------
@pytest.mark.parametrize("raw,want", [
    ("Brazilian Portuguese", "Brazilian Portuguese"),
    ("Simplified Chinese.", "Simplified Chinese"),
    ("Spanish", "Spanish"),
    ("UNKNOWN", ""), ("", ""),
    ("This text appears to be written in Spanish", ""),
    ("Spanish, probably", ""),
    ("Latin American Spanish dialect", ""),
    ("Mandarin Chinese Simplified", "Mandarin Chinese Simplified"),
    ("A" * 31, ""),
])
def test_parse_language_name(raw, want):
    assert claude.parse_language_name(raw) == want


def test_detect_language_accepts_multiword_name(monkeypatch):
    monkeypatch.setattr(claude, "is_live", lambda *_: True)
    monkeypatch.setattr(claude, "_call", _aret("Brazilian Portuguese"))
    assert asyncio.run(claude.detect_language("Reservei os bilhetes")) == \
        "Brazilian Portuguese"


def test_multiword_name_means_translation_stored_not_refused(monkeypatch):
    """End to end through the real detect_language (only the raw model call
    is mocked): a translation whose check answers a multi-word English-ish
    name is not discarded as unverified."""
    monkeypatch.setattr(claude, "is_live", lambda *_: True)
    monkeypatch.setattr(claude, "_call", _aret("Brazilian Portuguese"))
    monkeypatch.setattr(claude, "translate", _aret(GOOD))
    r = _review(lang="Brazilian Portuguese")
    asyncio.run(RL.translate_inbound(r, "r1"))
    # The detector now NAMES the language (wrong_language, a real verdict)
    # instead of "" (unverified): the outcome differs from the old behaviour.
    assert r.translation_state == "refused_wrong_language"
    # and when the detector says English, it is stored.
    monkeypatch.setattr(claude, "_call", _aret("English"))
    asyncio.run(RL.translate_inbound(r, "r1"))
    assert r.body_english == GOOD and r.translation_state == "stored"


# -- cause 2: verifier failure -----------------------------------------------
@pytest.mark.parametrize("verifier", [_aret(""), _araise(RuntimeError("429"))])
def test_good_translation_survives_verifier_failure(monkeypatch, verifier):
    monkeypatch.setattr(claude, "translate", _aret(GOOD))
    monkeypatch.setattr(claude, "detect_language", verifier)
    r = _review()
    trail = asyncio.run(RL.translate_inbound(r, "r2"))
    assert r.body_english == GOOD
    assert r.translation_state == "stored_unverified"
    assert trail["mark"] == "warn" and "UNVERIFIED" in trail["text"]


def test_verifier_down_and_text_not_english_is_still_refused(monkeypatch):
    monkeypatch.setattr(claude, "translate",
                        _aret("Reservei os bilhetes e adoramos o passeio."))
    monkeypatch.setattr(claude, "detect_language", _aret(""))
    r = _review()
    asyncio.run(RL.translate_inbound(r, "r3"))
    assert not r.body_english and r.translation_state == "refused_unverified"


@pytest.mark.parametrize("text,want", [
    (GOOD, True),
    ("Prawie podwójna cena za korzyści, które są wprowadzające.", False),
    ("Ho prenotato i biglietti e ho pagato il doppio.", False),
    ("", False), ("1234 !!!", False),
    ("Great tour", False),
    ("It was great", True),
])
def test_looks_english(text, want):
    assert RL.looks_english(text) is want


# -- wrong language still refused --------------------------------------------
def test_wrong_language_result_refused(monkeypatch):
    monkeypatch.setattr(claude, "translate", _aret("Prawie podwójna cena."))
    monkeypatch.setattr(claude, "detect_language", _aret("Polish"))
    r = _review()
    trail = asyncio.run(RL.translate_inbound(r, "r4"))
    assert not r.body_english
    assert r.translation_state == "refused_wrong_language"
    assert trail["mark"] == "warn" and "Polish" in trail["text"]


# -- cause 3: durable state, visible after re-run ----------------------------
def test_state_survives_and_is_overwritten_by_clean_rerun(monkeypatch):
    r = _review()
    monkeypatch.setattr(claude, "translate", _aret("Prawie podwójna cena."))
    monkeypatch.setattr(claude, "detect_language", _aret("Polish"))
    asyncio.run(RL.translate_inbound(r, "r5"))
    assert r.translation_state == "refused_wrong_language" and not r.body_english
    monkeypatch.setattr(claude, "translate", _aret(GOOD))
    monkeypatch.setattr(claude, "detect_language", _aret("English"))
    asyncio.run(RL.translate_inbound(r, "r5"))
    assert r.translation_state == "stored" and r.body_english == GOOD


def test_translate_crash_sets_failed(monkeypatch):
    monkeypatch.setattr(claude, "translate", _araise(RuntimeError("boom")))
    r = _review()
    trail = asyncio.run(RL.translate_inbound(r, "r6"))
    assert r.translation_state == "failed" and trail["mark"] == "warn"


def test_english_already_on_english_review_is_pass(monkeypatch):
    monkeypatch.setattr(claude, "translate", _aret("ENGLISH_ALREADY"))
    for lang in ("English", None, "en"):
        r = _review(lang=lang)
        trail = asyncio.run(RL.translate_inbound(r, "r7"))
        assert r.translation_state == "english_already"
        assert trail["mark"] == "pass"


def test_english_already_on_known_non_english_review_is_suspicious(monkeypatch):
    monkeypatch.setattr(claude, "translate", _aret("ENGLISH_ALREADY"))
    r = _review(lang="Italian")
    trail = asyncio.run(RL.translate_inbound(r, "r8"))
    assert r.translation_state == "english_already_suspicious"
    assert trail["mark"] == "warn" and "Italian" in trail["text"]
    assert not r.body_english


def test_looks_english_rejects_heavily_accented_text_even_with_stopwords():
    assert RL.looks_english("the and of to é è ü ö ä ß ñ ç ø å the and of to") is False


@pytest.mark.parametrize("detected,body_en,want", [
    ("English", "I paid double.", "stored"),
    ("Polish", "Prawie podwójna cena.", "refused_wrong_language"),
])
def test_vs_intake_records_translation_state(client, live_db, monkeypatch,
                                             detected, body_en, want):
    monkeypatch.setattr(claude, "detect_language", _aret(detected))
    r = client.post("/api/vs-intake",
                    json={"review": {"body_original": "Ho pagato il doppio.",
                                     "body_english": body_en}})
    rid = r.json()["review_id"]
    s = live_db.SessionLocal()
    try:
        got = s.query(live_db.Review).filter_by(id=rid).first().translation_state
        assert got == want
    finally:
        s.close()


def test_state_column_exists_and_is_migrated():
    from server import db
    assert hasattr(db.Review, "translation_state")
    assert "translation_state" in db._WANTED_REVIEW_COLUMNS(False)
