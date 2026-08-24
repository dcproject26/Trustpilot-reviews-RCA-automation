"""The macro selector: an AI reads the review and picks the scenario, with the
keyword scorer as the fallback.

Two halves, and they fail differently:

  * claude.select_reply_macro — parses the model's answer, and RAISES on
    no-model / bad output so the caller can fall back rather than silently send
    nothing. A model outage must degrade to the old keyword behaviour, never to
    "no approved macro matches" on a review that has one.
  * get_canned_responses — runs the DSS gate first, then the selector, and
    reports which selector produced the answer. "The AI judged nothing fits"
    and "the AI could not be reached" are the same empty list and different
    problems.

The model's JUDGEMENT is not testable here and is verified live; what is
testable is that the plumbing routes to the right place and says which route it
took.
"""
import asyncio

import pytest

from server.services import canned as C
from server.services import claude


CANDS = [{"i": 0, "situation": "Guide no show", "promises": ["credit_hoc"]},
         {"i": 1, "situation": "Unable to trace booking", "promises": []}]


def _run(**kw):
    return asyncio.run(claude.select_reply_macro(
        review_text="the guide never came", candidates=CANDS, **kw))


# ── the parse-and-raise contract ────────────────────────────────────────────

def test_no_model_raises_so_the_caller_can_fall_back(monkeypatch):
    monkeypatch.setattr(claude, "is_live", lambda name: False)
    with pytest.raises(RuntimeError):
        _run()


def test_empty_candidates_never_calls_the_model(monkeypatch):
    called = []
    async def _spy(*a, **k):
        called.append(1)
        return "{}"
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _spy)
    out = asyncio.run(claude.select_reply_macro(review_text="x", candidates=[]))
    assert out["index"] == -1
    assert not called, "the model was asked to choose from nothing"


def test_a_good_answer_is_parsed(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"index": 1, "confidence": "HIGH", "reason": "no booking id"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    out = _run()
    assert out == {"index": 1, "confidence": "high", "reason": "no booking id"}


def test_minus_one_is_a_real_no_match(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"index": -1, "confidence": "low", "reason": "nothing fits"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    assert _run()["index"] == -1


@pytest.mark.parametrize("body", [
    '{"index": "one", "confidence": "high", "reason": "x"}',
    '{"confidence": "high", "reason": "x"}',
    "sorry, I cannot help with that",
])
def test_unusable_output_raises(monkeypatch, body):
    async def _call(prompt, max_tokens=400):
        return body
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    with pytest.raises(Exception):
        _run()


# ── the prompt carries what the selector needs ──────────────────────────────

def test_the_prompt_shows_what_each_macro_offers():
    from server import prompts
    p = prompts.reply_macro_select_prompt("x", CANDS, dss_action="Offer HOC")
    assert "[offers: credit_hoc]" in p
    assert "offers nothing" in p, \
        "a remedy-free macro is indistinguishable from one that pays out"


def test_the_prompt_says_l1_l2_are_corroboration_not_the_key():
    """The manual-review cascade came from treating a missing L2 as decisive."""
    from server import prompts
    p = prompts.reply_macro_select_prompt("x", CANDS, l1="Ops", l2="Tickets")
    assert "corroboration" in p
    assert "-1" in p, "the model is never told it may decline to match"


# ── routing through get_canned_responses ────────────────────────────────────

ROWS = [
    {"situation": "Guide no show", "response": "I've added 50% HOC.",
     "tab": "ORM main ( TP ) Macro"},
    {"situation": "Unable to trace booking",
     "response": "Could you share your booking reference?",
     "tab": "ORM main ( TP ) Macro"},
]
DSS = {"action": "Offer 50% HOC in credits.", "match_score": 5}


def _canned(monkeypatch, **kw):
    async def _rows():
        return [dict(r) for r in ROWS]
    monkeypatch.setattr(C, "_get_rows", _rows)
    return asyncio.run(C.get_canned_responses(
        kw.pop("l1", "Supply Partner Issue"), kw.pop("l2", "Guide No Show"),
        "", kw.pop("review_text", "the guide never came"),
        dss_rec=kw.pop("dss_rec", DSS), **kw))


def test_the_ai_choice_is_returned_and_labelled(monkeypatch):
    async def _sel(**kw):
        return {"index": 0, "confidence": "high", "reason": "guide absent"}
    monkeypatch.setattr(claude, "select_reply_macro", _sel)
    got = _canned(monkeypatch)
    assert len(got) == 1, "the selector's pick must not arrive beside runners-up"
    assert got[0]["situation"] == "Guide no show"
    assert got[0]["selector"] == "ai"
    assert got[0]["selector_reason"] == "guide absent"


def test_the_ai_only_sees_macros_the_gate_allowed(monkeypatch):
    """The gate runs BEFORE the selector: a macro promising an unprescribed
    remedy must never be offered as an option, not merely rejected later."""
    seen = {}
    async def _sel(**kw):
        seen["situations"] = [c["situation"] for c in kw["candidates"]]
        return {"index": -1, "confidence": "low", "reason": "n/a"}
    monkeypatch.setattr(claude, "select_reply_macro", _sel)
    _canned(monkeypatch, dss_rec={"match_score": 0, "fallback": "none"})
    assert seen["situations"] == ["Unable to trace booking"], seen
    assert "Guide no show" not in seen["situations"], \
        "an unauthorised HOC macro was offered to the selector"


def test_the_ai_saying_none_fits_returns_nothing(monkeypatch):
    async def _sel(**kw):
        return {"index": -1, "confidence": "high", "reason": "unrelated complaint"}
    monkeypatch.setattr(claude, "select_reply_macro", _sel)
    assert _canned(monkeypatch) == []
    assert "unrelated complaint" in C.last_failure_reason()


def test_a_model_outage_falls_back_to_keywords_not_to_silence(monkeypatch):
    """The whole reason the scorer is kept. A transient failure must not read
    as "no approved macro matches this review"."""
    async def _boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr(claude, "select_reply_macro", _boom)
    got = _canned(monkeypatch)
    assert got, "a model outage produced no reply at all"
    assert got[0]["selector"] == "keyword-fallback"


def test_an_out_of_range_index_is_not_trusted(monkeypatch):
    async def _sel(**kw):
        return {"index": 99, "confidence": "high", "reason": "x"}
    monkeypatch.setattr(claude, "select_reply_macro", _sel)
    assert _canned(monkeypatch) == [], \
        "an index past the candidate list was used to pick a macro"


def test_everything_gated_out_is_not_reported_as_nothing_fitting(monkeypatch):
    """"No macro fits this review" and "every macro promised a remedy the
    playbook did not name" are the same empty list and different problems."""
    async def _rows():
        return [{"situation": "Guide no show", "response": "I've added 50% HOC.",
                 "tab": "ORM main ( TP ) Macro"}]
    monkeypatch.setattr(C, "_get_rows", _rows)
    got = asyncio.run(C.get_canned_responses(
        "x", "y", "", "the guide never came",
        dss_rec={"match_score": 0, "fallback": "none"}))
    assert got == []
    why = C.last_failure_reason()
    assert "withheld" in why or "promise" in why, why
