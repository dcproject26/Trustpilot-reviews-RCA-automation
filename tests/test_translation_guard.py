"""The inbound translation must be ENGLISH before it is stored as body_english.

The bug: an English review whose inbound `translate()` came back in Polish had
that Polish stored and rendered as "English translation · AI". The only check
was `!= ENGLISH_ALREADY`, so a wrong-language result and a real translation were
indistinguishable — CLAUDE.md rule 1. The record settled the cause: language was
"English" (detection read the ORIGINAL as English), so the prompt's first branch
was never the source; the translate call simply returned the wrong language and
nothing validated it.

Driven end to end: the guard function, the pipeline call site (process_review),
and the VectorShift intake — because a guard tested only as a function, with the
lines that call it untested, is this codebase's most repeated miss.
"""
import asyncio
import importlib
import json
import os
import tempfile
from datetime import datetime

import pytest

from server.services import claude, reply_language as RL
from tests.conftest import drop_temp_db


def _aret(v):
    async def _f(*a, **k):
        return v
    return _f


def _araise(exc):
    async def _f(*a, **k):
        raise exc
    return _f


# ── the guard, driven directly ──────────────────────────────────────────────

def test_confirmed_english_is_stored(monkeypatch):
    monkeypatch.setattr(claude, "detect_language", _aret("English"))
    v = asyncio.run(RL.english_or_reject("A perfectly ordinary English sentence."))
    assert v["store"] is True and v["outcome"] == "stored"


def test_a_wrong_language_result_is_refused(monkeypatch):
    monkeypatch.setattr(claude, "detect_language", _aret("Polish"))
    v = asyncio.run(RL.english_or_reject("Prawie podwójna cena za korzyści."))
    assert v["store"] is False
    assert v["outcome"] == "wrong_language"
    assert v["language"] == "Polish"


def test_an_unverifiable_result_is_refused_but_not_as_wrong_language(monkeypatch):
    """detector said nothing → UNVERIFIED, refused — and it must read
    differently from a named wrong language and from a genuine English store."""
    monkeypatch.setattr(claude, "detect_language", _aret(""))
    v = asyncio.run(RL.english_or_reject("some text"))
    assert v["store"] is False and v["outcome"] == "unverified"

    monkeypatch.setattr(claude, "detect_language", _araise(RuntimeError("529")))
    raised = asyncio.run(RL.english_or_reject("some text"))
    assert raised["store"] is False and raised["outcome"] == "unverified"


def test_the_three_refusals_and_the_store_all_read_differently(monkeypatch):
    """Rule 1: none of these four may collapse into another's wording."""
    monkeypatch.setattr(claude, "detect_language", _aret("English"))
    stored = asyncio.run(RL.english_or_reject("x"))["why"]
    monkeypatch.setattr(claude, "detect_language", _aret("Polish"))
    wrong = asyncio.run(RL.english_or_reject("x"))["why"]
    monkeypatch.setattr(claude, "detect_language", _aret(""))
    unver = asyncio.run(RL.english_or_reject("x"))["why"]
    assert len({stored, wrong, unver}) == 3


# ── the pipeline call site ──────────────────────────────────────────────────

def _run_pipeline(monkeypatch, *, body_original, translate_result, detected):
    """Seed one English-original review with an EMPTY body_english, stub the
    inbound translate to return `translate_result` and the detector to return
    `detected`, run process_review, and hand back (review, draft)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()

    from tests.test_pipeline_validates_its_rca import _stub, BASE
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    import sys
    pipe = sys.modules["server.pipeline"]

    # Override the two calls this test is about, AFTER _stub (which set
    # translate to a no-op). detect_language is used by the guard AND by
    # resolve_language; a single stub serves both.
    monkeypatch.setattr(claude, "translate", _aret(translate_result))
    monkeypatch.setattr(claude, "detect_language", _aret(detected))

    s = db.SessionLocal()
    s.add(db.Review(id="tp_g", slack_ts="tp_g", slack_channel="C1", rating=1,
                    author="A", body_original=body_original, body_english=None,
                    language=None, status="new", received_at=datetime.utcnow()))
    s.commit()
    s.close()

    asyncio.run(pipe.process_review("tp_g"))

    s = db.SessionLocal()
    r = s.query(db.Review).filter_by(id="tp_g").first()
    d = s.query(db.RcaDraft).filter_by(review_id="tp_g").first()
    review_english = r.body_english
    trail = [e for e in (d.confidence_trail or []) if isinstance(e, dict)]
    s.close()
    drop_temp_db(tmp.name)
    return review_english, trail


def test_pipeline_refuses_to_store_a_wrong_language_translation(monkeypatch):
    """THE ACTUAL BUG. English review, translate returns Polish, detector names
    it Polish — the Polish must NOT land in body_english, and the trail must
    carry a warn that says why."""
    english, trail = _run_pipeline(
        monkeypatch,
        body_original="I booked skip-the-line tickets and paid almost double.",
        translate_result="Prawie podwójna cena za korzyści, które są wprowadzające.",
        detected="Polish")
    assert not english, f"a wrong-language translation was stored: {english!r}"
    warns = [e for e in trail if e.get("mark") == "warn"
             and "Translation" in e.get("text", "")]
    assert warns, f"no translation warn on the trail: {trail}"
    assert "Polish" in warns[0]["text"]


def test_pipeline_stores_a_confirmed_english_translation(monkeypatch):
    english, trail = _run_pipeline(
        monkeypatch,
        body_original="Ho prenotato i biglietti e ho pagato il doppio.",
        translate_result="I booked the tickets and paid double.",
        detected="English")
    assert english == "I booked the tickets and paid double."
    assert any(e.get("mark") == "pass" and "Translation" in e.get("text", "")
               for e in trail), trail


def test_english_already_leaves_body_empty_and_says_none_needed(monkeypatch):
    """The other empty: nothing stored because none was NEEDED. It must not read
    like the wrong-language refusal, which also stores nothing."""
    english, trail = _run_pipeline(
        monkeypatch,
        body_original="Straightforward English review, nothing to translate.",
        translate_result="ENGLISH_ALREADY",
        detected="English")
    assert not english
    none_needed = [e for e in trail if "none" in e.get("text", "").lower()
                   and "Translation" in e.get("text", "")]
    assert none_needed and none_needed[0]["mark"] == "pass", trail


# ── the VectorShift call site ───────────────────────────────────────────────

def test_vs_intake_drops_the_untranslated_fallback(client, live_db, monkeypatch):
    """The `or body_original` fallback is gone: a payload with no body_english
    must NOT copy the untranslated original into the field the UI labels
    'English translation'. It stays empty."""
    r = client.post("/api/vs-intake",
                    json={"review": {"body_original": "Ho pagato il doppio."}})
    assert r.status_code == 200, r.text
    rid = r.json()["review_id"]
    got = client.get(f"/api/reviews/{rid}").json()["review"]
    assert not got["body_english"], got["body_english"]


def test_vs_intake_refuses_a_wrong_language_body_english(client, live_db, monkeypatch):
    monkeypatch.setattr(claude, "detect_language", _aret("Polish"))
    r = client.post("/api/vs-intake",
                    json={"review": {"body_original": "English original.",
                                     "body_english": "Prawie podwójna cena."}})
    rid = r.json()["review_id"]
    got = client.get(f"/api/reviews/{rid}").json()["review"]
    assert not got["body_english"], "a wrong-language body_english was stored"


def test_vs_intake_keeps_a_confirmed_english_body_english(client, live_db, monkeypatch):
    monkeypatch.setattr(claude, "detect_language", _aret("English"))
    r = client.post("/api/vs-intake",
                    json={"review": {"body_original": "Ho pagato il doppio.",
                                     "body_english": "I paid double."}})
    rid = r.json()["review_id"]
    got = client.get(f"/api/reviews/{rid}").json()["review"]
    assert got["body_english"] == "I paid double."
