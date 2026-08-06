"""Applying an English edit rewrites the reply that goes to the guest.

The English box is a projection. Editing it is not a save — it is a request to
rewrite the outgoing reply through a translation, and the contract that
matters is what happens when that translation fails: the outgoing reply must
be left EXACTLY as it was, and the caller must be told the English edit was
not applied and why.

"An edit that appears to save and did not" is what this codebase punishes
hardest, so every failure path below asserts the stored reply is byte-for-byte
what it was before the call.
"""
import asyncio
from datetime import datetime

import pytest

from server.services.reply_language import digest


BEFORE_IT = "Ci dispiace molto per l'attesa."
BEFORE_EN = "We are very sorry about the wait."


def _seed(db, language="IT", final=BEFORE_IT, english=None, of=None):
    s = db.SessionLocal()
    # The body has to MATCH the declared language: a review filed as EN whose
    # text was translated on the way in is not English, and language_state
    # says so — see test_reply_language.py.
    _orig = ("They never arrived" if (language or "").upper() == "EN"
             else "Non sono mai arrivati")
    s.add(db.Review(id="tp_x", slack_ts="9.0", slack_channel="C_MOCK_ORM",
                    rating=2, author="Luca", language=language,
                    body_original=_orig,
                    body_english="They never arrived", status="draft",
                    received_at=datetime(2026, 8, 1)))
    s.add(db.RcaDraft(id="draft_tp_x", review_id="tp_x",
                      final_response=final, suggested_response="",
                      response_english=english, response_english_of=of))
    s.commit()
    s.close()


def _stored(db):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_x").first()
        return (d.final_response, d.response_english, d.response_english_of)
    finally:
        s.close()


def _call(english="They never arrived at all."):
    """Drive the real endpoint function against the live session."""
    import server.api as api
    import server.db as db
    s = db.SessionLocal()
    try:
        return asyncio.run(api.apply_english_reply(
            "tp_x", api.EnglishReplyBody(english=english), s))
    finally:
        s.close()


@pytest.fixture()
def api_db(live_db):
    """The throwaway database, with NO module reload.

    `importlib.reload(server.api)` was the obvious way to make the endpoint
    see the reloaded `server.db`, and it poisoned every later test file in the
    same process: the reloaded module keeps module-level bindings to an engine
    whose file is deleted at teardown, so twenty-nine tests in
    test_one_store_per_v4_section.py failed only when this file ran first.

    It is not needed. `_call` builds the session from the reloaded `server.db`
    and hands it to the endpoint, and the ORM classes server.api already holds
    map to the same table names — so the queries run against the temp engine
    through the session that was passed in, which is the only binding that
    decides where they go.
    """
    return live_db


# ── The success path ────────────────────────────────────────────────────────

def test_an_english_edit_rewrites_the_outgoing_reply(api_db, monkeypatch):
    import server.services.claude as claude

    async def _t(text, lang, review_id=None):
        assert lang == "IT"
        return "Non sono mai arrivati affatto."
    monkeypatch.setattr(claude, "translate_to", _t)

    _seed(api_db)
    out = _call()
    assert out["ok"] and out["translated"] is True
    final, eng, of = _stored(api_db)
    assert final == "Non sono mai arrivati affatto."
    assert eng == "They never arrived at all."
    assert of == digest(final)


def test_the_response_says_which_text_goes_to_the_guest(api_db, monkeypatch):
    import server.services.claude as claude
    monkeypatch.setattr(claude, "translate_to",
                        lambda t, l, r=None: _coro("Non sono mai arrivati affatto."))
    _seed(api_db)
    out = _call()
    assert out["outgoing"] == "Non sono mai arrivati affatto."
    assert "goes to the guest" in out["note"]


def _coro(v):
    async def _inner():
        return v
    return _inner()


# ── THE FAILURE CONTRACT ────────────────────────────────────────────────────

def test_a_raising_translation_leaves_the_outgoing_reply_untouched(api_db, monkeypatch):
    import server.services.claude as claude

    async def _boom(text, lang, review_id=None):
        raise RuntimeError("upstream 502")
    monkeypatch.setattr(claude, "translate_to", _boom)

    _seed(api_db)
    with pytest.raises(Exception) as e:
        _call()
    assert "NOT applied" in str(e.value)
    assert "unchanged" in str(e.value)
    # Byte for byte what it was.
    assert _stored(api_db) == (BEFORE_IT, None, None)


def test_an_empty_translation_leaves_the_outgoing_reply_untouched(api_db, monkeypatch):
    """A call that returns nothing is a failure, not an empty reply. Writing
    "" would blank the guest response and look like a successful save."""
    import server.services.claude as claude

    async def _nothing(text, lang, review_id=None):
        return ""
    monkeypatch.setattr(claude, "translate_to", _nothing)

    _seed(api_db)
    with pytest.raises(Exception) as e:
        _call()
    assert "NOT applied" in str(e.value)
    assert _stored(api_db) == (BEFORE_IT, None, None)


def test_the_failure_names_what_would_work(api_db, monkeypatch):
    """An error should name the way out. "Translation failed" leaves someone
    stuck; "edit the IT reply directly" is the useful version."""
    import server.services.claude as claude

    async def _boom(text, lang, review_id=None):
        raise RuntimeError("upstream 502")
    monkeypatch.setattr(claude, "translate_to", _boom)

    _seed(api_db)
    with pytest.raises(Exception) as e:
        _call()
    assert "IT" in str(e.value)
    assert "directly" in str(e.value)


def test_a_failure_does_not_half_apply_the_english(api_db, monkeypatch):
    """The half-apply — English stored, outgoing untouched — is the shape that
    leaves a card showing an edit that will never reach the guest."""
    import server.services.claude as claude

    async def _boom(text, lang, review_id=None):
        raise RuntimeError("nope")
    monkeypatch.setattr(claude, "translate_to", _boom)

    _seed(api_db, english=BEFORE_EN, of=digest(BEFORE_IT))
    with pytest.raises(Exception):
        _call("Something completely different.")
    final, eng, of = _stored(api_db)
    assert final == BEFORE_IT
    assert eng == BEFORE_EN          # the OLD projection, not the failed edit
    assert of == digest(BEFORE_IT)


# ── An English review has one box ───────────────────────────────────────────

def test_an_english_review_stores_the_text_and_translates_nothing(api_db, monkeypatch):
    import server.services.claude as claude
    calls = []

    async def _t(text, lang, review_id=None):
        calls.append(lang)
        return "should not happen"
    monkeypatch.setattr(claude, "translate_to", _t)

    _seed(api_db, language="EN", final="Sorry about that.")
    out = _call("We are sorry about the wait.")
    assert out["translated"] is False
    assert calls == []
    final, eng, of = _stored(api_db)
    assert final == "We are sorry about the wait."
    # No projection at all: there is one box, and a stored English projection
    # beside an English reply would imply a translation happened.
    assert eng is None and of is None


def test_an_english_review_says_nothing_was_translated(api_db, monkeypatch):
    _seed(api_db, language="EN", final="Sorry.")
    out = _call("Sorry about the wait.")
    assert "nothing was translated" in out["note"]
    assert out["english_state"] == "same"


# ── A review with no language refuses rather than guessing ──────────────────

def test_no_language_refuses_and_leaves_the_reply_alone(api_db, monkeypatch):
    """Translating into a language we do not know is not possible, and
    defaulting to English is how a guest gets a reply they cannot read."""
    import server.services.claude as claude
    calls = []

    async def _t(text, lang, review_id=None):
        calls.append(lang)
        return "x"
    monkeypatch.setattr(claude, "translate_to", _t)

    _seed(api_db, language="", final=BEFORE_IT)
    with pytest.raises(Exception) as e:
        _call()
    assert calls == []
    assert "no language" in str(e.value).lower()
    assert "unchanged" in str(e.value)
    assert _stored(api_db)[0] == BEFORE_IT


def test_no_language_names_what_would_fix_it(api_db):
    _seed(api_db, language="")
    with pytest.raises(Exception) as e:
        _call()
    assert "Review.language" in str(e.value)


# ── Empty input ─────────────────────────────────────────────────────────────

def test_an_empty_english_box_is_refused_without_touching_the_reply(api_db):
    _seed(api_db)
    with pytest.raises(Exception):
        _call("   ")
    assert _stored(api_db)[0] == BEFORE_IT
