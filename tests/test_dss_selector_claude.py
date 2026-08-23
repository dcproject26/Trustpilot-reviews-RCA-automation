"""Unit tests for claude.select_dss_scenario — the DSS AI selector's parsing
and failure contract, driven WITHOUT a live model.

The selector must RAISE on no-model / bad output (so dss.get_recommendation
falls back to the keyword scorer) and return a clean {index, confidence, reason}
on good output. These mock claude._call and claude.is_live so the logic is
exercised deterministically; the model's *judgement* is not testable here and is
verified live.
"""
import asyncio

import pytest

from server.services import claude


CANDS = [{"i": 0, "scenario": "guide absent", "action": "refund"},
         {"i": 1, "scenario": "venue closed", "action": "reschedule"}]


def _run(**kw):
    return asyncio.run(claude.select_dss_scenario(
        situation="the guide never showed", candidates=CANDS, **kw))


def test_no_model_raises_so_dss_can_fall_back(monkeypatch):
    monkeypatch.setattr(claude, "is_live", lambda name: False)
    with pytest.raises(RuntimeError):
        _run()


def test_empty_candidates_short_circuits_without_calling_the_model(monkeypatch):
    called = {"n": 0}
    async def _spy(*a, **k):
        called["n"] += 1
        return "{}"
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _spy)
    out = asyncio.run(claude.select_dss_scenario(
        situation="x", candidates=[]))
    assert out["index"] == -1
    assert called["n"] == 0, "model was called for an empty candidate list"


def test_good_json_is_parsed(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"index": 1, "confidence": "HIGH", "reason": "venue was shut"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    out = _run()
    assert out["index"] == 1
    assert out["confidence"] == "high"          # lower-cased
    assert out["reason"] == "venue was shut"


def test_index_minus_one_is_a_valid_no_match(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"index": -1, "confidence": "low", "reason": "nothing fits"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    assert _run()["index"] == -1


def test_non_int_index_raises(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"index": "one", "confidence": "high", "reason": "x"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    with pytest.raises(ValueError):
        _run()


def test_missing_index_key_raises(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return '{"confidence": "high", "reason": "x"}'
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    with pytest.raises(ValueError):
        _run()


def test_unparseable_output_raises(monkeypatch):
    async def _call(prompt, max_tokens=400):
        return "sorry, I cannot help with that"
    monkeypatch.setattr(claude, "is_live", lambda name: True)
    monkeypatch.setattr(claude, "_call", _call)
    with pytest.raises((ValueError, Exception)):
        _run()
