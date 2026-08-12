"""The card works out the guest's language instead of asking for it.

What this replaces: a "Guest's language — e.g. Spanish" text input sitting
under a reply that was in English, on a review whose original text is French
and is stored in `body_original`. The associate was being asked to identify a
language from text the row already holds.

The endpoint does two things and the second only runs if the first works:
read the language off the ORIGINAL review text and record it, then — when the
reply is still sitting in English on a review that is not — translate it, so
the two boxes arrive in the state they should already have been in.

The contract is the same one `apply-english-reply` has: NOTHING half-written.
A translation that fails leaves the reply byte-for-byte as it was, and the
response says the reply is unchanged and why.
"""
import asyncio
from datetime import datetime

import pytest


FR_ORIG = "Ils ne sont jamais arrivés."
REPLY_EN = "We are very sorry about the wait."
REPLY_FR = "Nous sommes vraiment désolés pour l'attente."


def _seed(db, language="en", final=REPLY_EN, english=None,
          orig=FR_ORIG, eng_body="They never arrived."):
    s = db.SessionLocal()
    s.add(db.Review(id="tp_x", slack_ts="9.0", slack_channel="C_MOCK_ORM",
                    rating=2, author="Bénédicte", language=language,
                    body_original=orig, body_english=eng_body, status="draft",
                    received_at=datetime(2026, 8, 1)))
    s.add(db.RcaDraft(id="draft_tp_x", review_id="tp_x",
                      final_response=final, suggested_response="",
                      response_english=english, response_english_of=None))
    s.commit()
    s.close()


def _stored(db):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_x").first()
        r = s.query(db.Review).filter(db.Review.id == "tp_x").first()
        return (r.language, d.final_response, d.response_english)
    finally:
        s.close()


def _call():
    import server.api as api
    import server.db as db
    s = db.SessionLocal()
    try:
        return asyncio.run(api.resolve_reply_language("tp_x", s))
    finally:
        s.close()


@pytest.fixture()
def api_db(live_db):
    # No module reload — see the note in test_apply_english_reply.py. The
    # session built from the reloaded server.db is handed to the endpoint, and
    # that is the binding that decides which engine the queries hit.
    return live_db


@pytest.fixture()
def detects(monkeypatch):
    """Install a detection answer and a translation, recording both calls."""
    calls = {"detect": [], "translate": []}

    def _install(language="French", translation=REPLY_FR):
        import server.services.claude as claude

        async def _d(text):
            calls["detect"].append(text)
            return language

        async def _t(text, lang, review_id=None):
            calls["translate"].append((text, lang))
            if isinstance(translation, Exception):
                raise translation
            return translation

        monkeypatch.setattr(claude, "detect_language", _d)
        monkeypatch.setattr(claude, "translate_to", _t)
        return calls
    return _install


# ── the whole point: nobody types the language ──────────────────────────────

def test_the_language_is_read_from_the_guests_own_words(api_db, detects):
    calls = detects("French")
    _seed(api_db)
    out = _call()
    assert out["outcome"] == "detected"
    assert calls["detect"] and FR_ORIG in calls["detect"][0], \
        "the detector was given something other than the guest's original text"
    lang, final, eng = _stored(api_db)
    assert lang == "French", "the language was detected and not recorded"


def test_the_reply_is_translated_in_the_same_call(api_db, detects):
    """The state the card should already be in for a non-English review: the
    outgoing box in the guest's language, the working copy in English. Reached
    without anybody pressing anything."""
    calls = detects("French", REPLY_FR)
    _seed(api_db)
    out = _call()
    assert out["translated"] is True
    assert calls["translate"] == [(REPLY_EN, "French")]
    lang, final, eng = _stored(api_db)
    assert final == REPLY_FR, "the outgoing reply is still English"
    assert eng == REPLY_EN, "the English working copy was not kept"
    assert out["outgoing"] == REPLY_FR


def test_the_card_gets_back_the_state_it_should_render(api_db, detects):
    """The client re-renders from this response rather than re-deriving it —
    two places computing "which box is current" is the divergence this whole
    area exists to stop."""
    detects("French")
    _seed(api_db)
    out = _call()
    assert out["response_language"]["state"] == "translated"
    assert out["response_language"]["language"] == "French"
    assert out["english_view"]["state"] == "current"
    assert out["english_view"]["text"] == REPLY_EN


# ── the failure paths leave the reply exactly as it was ─────────────────────

def test_a_failed_translation_leaves_the_reply_untouched(api_db, detects):
    detects("French", RuntimeError("upstream down"))
    _seed(api_db)
    out = _call()
    assert out["translated"] is False
    lang, final, eng = _stored(api_db)
    assert final == REPLY_EN, "the reply was changed on a failed translation"
    assert not eng, "an English projection was stamped against nothing"


def test_a_failed_translation_still_keeps_the_language_it_found(api_db, detects):
    """The language is a true fact about the review regardless of whether the
    reply could be rewritten, and re-deriving it costs another model call."""
    detects("French", RuntimeError("upstream down"))
    _seed(api_db)
    _call()
    assert _stored(api_db)[0] == "French"


def test_a_failed_translation_says_the_reply_is_still_english(api_db, detects):
    """Not "failed". The reader has to know WHICH text is in the box now."""
    detects("French", RuntimeError("upstream down"))
    _seed(api_db)
    out = _call()
    assert "NOT translated" in out["note"], out["note"]
    assert "still the" in out["note"] and "English" in out["note"], out["note"]


def test_an_empty_translation_is_a_failure_not_an_empty_reply(api_db, detects):
    detects("French", "   ")
    _seed(api_db)
    out = _call()
    assert out["translated"] is False
    assert _stored(api_db)[1] == REPLY_EN


# ── what it declines to do ──────────────────────────────────────────────────

def test_an_english_review_is_detected_once_and_then_left_alone(api_db, detects):
    """IT IS ASKED NOW, and that is the change. The old rule read "no inbound
    translation happened" as positive evidence of English — but an empty
    body_english is equally what a CRASHED translate leaves behind, so it
    declared English on a failed lookup and drew one box for a guest who may
    not read English.

    Asking costs one detect call per review, not one per render: the answer is
    stored as a NAME and every later call skips on `skipped_known`. Nothing is
    translated, because the review really is English."""
    calls = detects("English")
    _seed(api_db, orig="They never arrived.", eng_body="They never arrived.")
    out = _call()
    assert out["outcome"] == "detected"
    assert out["language"] == "English"
    assert len(calls["detect"]) == 1
    assert not calls["translate"], "an English reply was sent for translation"
    assert _stored(api_db)[1] == REPLY_EN

    out2 = _call()
    assert out2["outcome"] == "skipped_known"
    assert len(calls["detect"]) == 1, "a stored language was detected again"


def test_an_undetectable_language_is_reported_and_nothing_is_written(
        api_db, detects, monkeypatch):
    """Anthropic is deliberately forced LIVE here. `detect_language` returns
    "" both when it is switched off and when it read the review and could not
    place it, and those are different problems — one is escalated, the other
    is this review being hard. Without this the sandbox's offline Anthropic
    would make every empty answer read as `unavailable`."""
    import server.config as _cfg
    monkeypatch.setattr(_cfg, "is_live", lambda name: name == "anthropic")
    calls = detects("")
    _seed(api_db)
    out = _call()
    assert out["outcome"] == "undetected"
    assert calls["detect"], "it reported 'could not tell' without asking"
    assert not calls["translate"], "it translated into a language it does not know"
    lang, final, eng = _stored(api_db)
    assert lang == "en" and final == REPLY_EN
    assert "could not be named" in out["note"], out["note"]


def test_a_detector_that_is_not_connected_is_not_an_undetectable_review(
        api_db, detects):
    """The sandbox's own state, asserted rather than tolerated: Anthropic is
    offline, so this must come back as a DEPLOYMENT problem naming itself, not
    as a review whose language is hard."""
    import server.config as _cfg
    assert not _cfg.is_live("anthropic"), "this test needs Anthropic offline"
    detects("")
    _seed(api_db)
    out = _call()
    assert out["outcome"] == "unavailable", out
    assert "not connected" in out["note"], out["note"]
    assert _stored(api_db)[0] == "en", "a language was invented"


def test_an_existing_english_copy_is_not_overwritten(api_db, detects):
    """Somebody has already edited the pair. Retranslating the outgoing reply
    over the top would silently discard that edit."""
    calls = detects("French")
    _seed(api_db, final=REPLY_FR, english="An edit somebody made")
    out = _call()
    assert out["translated"] is False
    assert not calls["translate"]
    assert _stored(api_db)[2] == "An edit somebody made"


def test_a_review_with_no_drafted_reply_is_not_an_error(api_db, detects):
    """Nothing to translate is not a failure, and a 500 here would read as
    "the language check broke" on a card whose reply is deliberately blank."""
    detects("French")
    _seed(api_db, final="")
    out = _call()
    assert out["ok"] is True and out["translated"] is False
    assert "no drafted reply" in out["note"], out["note"]
    assert _stored(api_db)[0] == "French", \
        "the language was found and thrown away because the reply was empty"


def test_a_missing_review_is_a_404(api_db, detects):
    from fastapi import HTTPException
    detects("French")
    with pytest.raises(HTTPException) as e:
        _call()
    assert e.value.status_code == 404
