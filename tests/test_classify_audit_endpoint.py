"""/api/classify-audit — the endpoint the sheet's Apps Script posts to.

The scoring itself is driven and mutation-tested in test_classifier_audit.py.
These tests pin the WIRING: that the live classifier's answer reaches the
scorer, that a correct row and a wrong one come back different, and — the whole
point of this codebase — that a row the model could NOT reach comes back as an
absence, never as a silent miss, and that a dead model REFUSES rather than
scoring a column of blanks.

`classify` is stubbed, because the real one needs a live model this container
does not have. `is_live` is forced True so the endpoint gets past its own guard
to the part these tests are about.
"""
import pytest

import server.api as api_mod
from server.services import classifier as classifier_mod
from server.services.classifier import ClassificationResult


def _stub_classify(monkeypatch, fn):
    monkeypatch.setattr(classifier_mod, "classify", fn)


def _live(monkeypatch, on=True):
    # api.py binds is_live at module import, so patch it where the endpoint
    # looks it up — not on server.config.
    monkeypatch.setattr(api_mod, "is_live", lambda svc: on)


def _row(review="the guide never showed up", l1="Supply Partner Issue",
         l2="Guide Issues", sub_theme="A. Guide No Show", rid="r1"):
    return {"review_id": rid, "review": review, "l1": l1, "l2": l2,
            "sub_theme": sub_theme}


# ── the model's answer reaches the scorer ───────────────────────────────────

def test_a_correct_prediction_scores_yes_all_the_way(client, monkeypatch):
    _live(monkeypatch)

    async def ok(text, booking, timeline, call, rid=None):
        return ClassificationResult(l1="Supply Partner Issue", l2="Guide Issues",
                                    sub_theme="A. Guide No Show")
    _stub_classify(monkeypatch, ok)

    r = client.post("/api/classify-audit", json={"rows": [_row()]})
    assert r.status_code == 200, r.text
    out = r.json()
    res = out["results"][0]
    assert res["pred_l1"] == "Supply Partner Issue"
    assert res["l1_ok"] == "yes" and res["l2_ok"] == "yes"
    assert res["miss_bucket"] == ""
    assert out["summary"]["l1"]["hits"] == 1


def test_a_wrong_l1_comes_back_scored_and_bucketed(client, monkeypatch):
    _live(monkeypatch)

    async def wrong(text, booking, timeline, call, rid=None):
        return ClassificationResult(l1="Operations Issue", l2="Timing")
    _stub_classify(monkeypatch, wrong)

    r = client.post("/api/classify-audit", json={"rows": [_row()]})
    res = r.json()["results"][0]
    assert res["l1_ok"] == "no"
    assert res["miss_bucket"] == "l1l2-boundary"


# ── an absence is never a miss ──────────────────────────────────────────────

def test_a_classifier_error_is_did_not_run_not_a_wrong_answer(client, monkeypatch):
    """THE POINT. classify raised — the row was not scored. It must not come
    back as l1_ok=no (a miss the model owns); it comes back blank with the
    reason, and the summary counts it as failed, not as a clean zero."""
    _live(monkeypatch)

    async def boom(text, booking, timeline, call, rid=None):
        raise RuntimeError("529 overloaded")
    _stub_classify(monkeypatch, boom)

    r = client.post("/api/classify-audit", json={"rows": [_row()]})
    out = r.json()
    res = out["results"][0]
    assert res["l1_ok"] == "" and res["l2_ok"] == "" and res["sub_ok"] == ""
    assert res["miss_bucket"] == "did-not-run"
    assert "529 overloaded" in res["warnings"]
    assert out["summary"]["rows_failed"] == 1
    assert out["summary"]["rows_scored"] == 0
    assert out["summary"]["l1"]["pct"] is None


def test_a_blank_review_is_not_sent_to_the_model(client, monkeypatch):
    _live(monkeypatch)
    called = {"n": 0}

    async def counted(text, booking, timeline, call, rid=None):
        called["n"] += 1
        return ClassificationResult(l1="Operations Issue", l2="Timing")
    _stub_classify(monkeypatch, counted)

    r = client.post("/api/classify-audit",
                    json={"rows": [_row(review="   ")]})
    res = r.json()["results"][0]
    assert called["n"] == 0, "an empty review was still sent to the classifier"
    assert res["miss_bucket"] == "did-not-run"
    assert "no review text" in res["warnings"]


# ── the two refusals ────────────────────────────────────────────────────────

def test_a_dead_model_refuses_rather_than_scoring_blanks(client, monkeypatch):
    _live(monkeypatch, on=False)

    async def never(*a, **k):
        raise AssertionError("classify must not be called when the model is down")
    _stub_classify(monkeypatch, never)

    r = client.post("/api/classify-audit", json={"rows": [_row()]})
    assert r.status_code == 503
    assert "not live" in r.text


def test_a_wrong_key_is_401_when_a_key_is_set(client, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setenv("AUDIT_API_KEY", "s3cret")

    async def ok(*a, **k):
        return ClassificationResult(l1="Operations Issue", l2="Timing")
    _stub_classify(monkeypatch, ok)

    bad = client.post("/api/classify-audit", json={"rows": [_row()]},
                      headers={"X-Audit-Key": "wrong"})
    assert bad.status_code == 401

    good = client.post("/api/classify-audit", json={"rows": [_row()]},
                       headers={"X-Audit-Key": "s3cret"})
    assert good.status_code == 200


def test_many_rows_all_come_back(client, monkeypatch):
    _live(monkeypatch)

    async def ok(text, booking, timeline, call, rid=None):
        return ClassificationResult(l1="Operations Issue", l2="Timing")
    _stub_classify(monkeypatch, ok)

    rows = [_row(rid=f"r{i}") for i in range(7)]
    r = client.post("/api/classify-audit", json={"rows": rows})
    out = r.json()
    assert len(out["results"]) == 7
    assert {x["review_id"] for x in out["results"]} == {f"r{i}" for i in range(7)}
