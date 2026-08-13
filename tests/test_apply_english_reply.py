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
    # The body has to MATCH the declared language: a review recorded as
    # English whose text was translated on the way in is NOT English, and
    # language_state says so — see test_reply_language.py. "en" is the ingest
    # default and means nobody looked, so it lands in `unknown` either way.
    _orig = ("They never arrived"
             if (language or "").lower() in ("en", "english")
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


def _call(english="They never arrived at all.", language=None):
    """Drive the real endpoint function against the live session.

    `language` is what the CARD supplies in the unknown-language case: the
    review is known not to be English (its text was translated inbound) and
    only the NAME was missing, which the associate reading the review can
    see. Passing it is what unblocks the rewrite.
    """
    import server.api as api
    import server.db as db
    s = db.SessionLocal()
    try:
        return asyncio.run(api.apply_english_reply(
            "tp_x", api.EnglishReplyBody(english=english, language=language), s))
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

    _seed(api_db, language="English", final="Sorry about that.")
    out = _call("We are sorry about the wait.")
    assert out["translated"] is False
    assert calls == []
    final, eng, of = _stored(api_db)
    assert final == "We are sorry about the wait."
    # No projection at all: there is one box, and a stored English projection
    # beside an English reply would imply a translation happened.
    assert eng is None and of is None


def test_an_english_review_says_nothing_was_translated(api_db, monkeypatch):
    _seed(api_db, language="English", final="Sorry.")
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
    assert "translated into english on the way in" in str(e.value).lower()
    assert "unchanged" in str(e.value)
    assert _stored(api_db)[0] == BEFORE_IT


def test_no_language_names_what_would_fix_it(api_db):
    """"An error should name what would work."

    THE REMEDY MOVED, THE GUARANTEE DID NOT. This used to insist on the
    literal string "Review.language", which was the only route at the time:
    someone had to go and set the column. The card can now supply the name
    itself, so the message points at the control the reader is looking at
    rather than at a database field they cannot reach — and it still names the
    fallback of writing the reply in the guest's language directly.

    THE REMEDY MOVED A SECOND TIME, and this is why. The message went on to
    say "Name the guest's language on the card" long after the card's language
    input had been deleted in favour of automatic detection — so it named a
    control the reader cannot find. Pointing someone at a field that is not
    there is worse than saying nothing: they look for it, and conclude the
    page is broken.

    The route that always works is typing the reply in the top box, which is
    the text that gets sent and saves as it is written.
    """
    _seed(api_db, language="")
    with pytest.raises(Exception) as e:
        _call()
    msg = str(e.value)
    assert "top box" in msg, msg
    assert "gets sent" in msg, msg
    assert "Name the guest's language on the card" not in msg, (
        "the message points at a control the card does not have")


def test_the_card_really_has_no_language_field_to_point_at():
    """NEGATIVE source assertion on CLIENT-SIDE JAVASCRIPT, which has no test
    harness here — the only shape of source assertion this repo allows besides
    a negative, and it is both.

    This is the fact the message above depends on. If a language input is ever
    added back, the wording should point at it again, and this fails to say
    so."""
    html = open("client/index.html", encoding="utf-8").read()
    assert "apply-english-reply" in html, "the call site moved; re-check this"
    assert "data-lang-input" not in html, \
        "a language field exists again — the 409 message should name it"


def test_naming_the_language_on_the_card_unblocks_the_rewrite(api_db):
    """The other half of the same change, and the reason the message moved.

    A review whose text was translated inbound is KNOWN not to be English;
    only the language code was missing. Refusing outright left the associate
    with no route at all from the card they were looking at.
    """
    _seed(api_db, language="")
    out = _call(language="Italian")
    assert out["ok"] is True, out
    assert out["translated"] is True, out
    assert out["language"] == "Italian", out


def test_the_named_language_is_recorded_so_it_is_asked_for_once(api_db):
    """Otherwise every later render asks again, and a fact the associate
    already established is thrown away on each pass."""
    import server.db as db
    _seed(api_db, language="")
    _call(language="Italian")
    s = db.SessionLocal()
    try:
        r = s.query(db.Review).filter(db.Review.id == "tp_x").first()
        assert (r.language or "").strip() == "Italian", r.language
    finally:
        s.close()


def test_a_blank_language_is_not_taken_as_an_answer(api_db):
    """Whitespace is not a language. Accepting it would record "" and leave
    the card in a state where it has stopped asking and still cannot
    translate."""
    _seed(api_db, language="")
    with pytest.raises(Exception) as e:
        _call(language="   ")
    assert "top box" in str(e.value)


# ── Empty input ─────────────────────────────────────────────────────────────

def test_an_empty_english_box_is_refused_without_touching_the_reply(api_db):
    _seed(api_db)
    with pytest.raises(Exception):
        _call("   ")
    assert _stored(api_db)[0] == BEFORE_IT
