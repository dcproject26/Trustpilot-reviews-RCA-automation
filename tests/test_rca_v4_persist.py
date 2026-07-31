"""The v4 output has to survive the trip to the database and back.

Two things go wrong silently here. A validation layer that nothing calls looks
exactly like one that works — every test passes, and raw model tokens still
reach the screen. And a second write path that drifts from the first leaves
half the columns holding the previous run's answer, which reads as data rather
than as staleness.

`regenerate-rca` is the one persist path that can be driven end to end without
the whole pipeline, so it is the one exercised for real. The pipeline's own
save is asserted at source, since it lives inside a 700-line coroutine that
cannot be entered without Zendesk, BigQuery and Anthropic.
"""
import asyncio
import importlib
import os
import tempfile

import pytest


PIPE = open("server/pipeline.py", encoding="utf-8").read()
API  = open("server/api.py", encoding="utf-8").read()

# What the model returns: every enum wrong, in the ways the handoff observed.
DIRTY_RCA = {
    "stated_issue": "The voucher never arrived.",
    "tldr": {"our_mistake": "We did not send it.", "our_fix": "Refunded."},
    "l1": "Refund Issue", "l2": "Delayed Refund",          # not in the taxonomy
    "sub_themes": ["C. Ticket Delayed"],
    "scenarios": ["Ticket delivery delay"],
    "what_went_wrong": {"guest_issues": [{
        "issue": "Voucher never delivered",
        "claim": "I waited two hours and nothing came.",
        "claim_accuracy": "probably fine tbh",              # → Unknown
        "owner": "RO",
        "root_cause": "The fulfilment run failed silently.",
        "evidence": ["[booking] Two adult tickets, unissued."],   # legacy string
    }]},
    "issue_specific_answers": [
        {"question": "How long until first reply?",
         "verdict": "28 minutes (first agent)"},            # → evidence
    ],
    "sop_compliance": {"verdict": "mostly followed"},       # → unknown
    "booking_logs": [{"time": "Unknown", "what": "Fulfilment attempted"}],
    "flags": [{"team": "Growth", "flag": "No alert on failed fulfilment",
               "evidence": "Three retries, no page."}],     # team → null
    "takedown": {"verdict": "maybe"},                       # → Untraceable
    "dss": {"prescribes": "Refund in full.", "ref": None},
    "resolution": "Full refund of EUR 84.",
    "suggested_response": "I'm sorry your tickets never arrived. "
                          "We have refunded you in full.",
}


@pytest.fixture()
def app_env(monkeypatch):
    """A throwaway SQLite DB with the real schema, and an api bound to it."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    yield db, api
    os.unlink(tmp.name)


def _seed(db, rid="tp_v4_1"):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="David Test", body_original="no voucher ever came",
                        status="draft"))
        s.add(db.RcaDraft(id=f"draft_{rid}", review_id=rid,
                          l1="Operations Issue", l2="Ticket Issues",
                          # what a previous run left behind
                          flags=[{"team": "CE", "flag": "stale"}],
                          resolution="Nothing offered yet",
                          suggested_response="an older reply"))
        s.commit()
    finally:
        s.close()
    return rid


def _regenerate(db, api, rid, rca=DIRTY_RCA, seen=None):
    async def _fake_rca(**kw):
        if seen is not None:
            seen.update(kw)
        return dict(rca)

    from server.services import claude as claude_svc
    from server.services import rca_checklist
    claude_svc.generate_rca_v3 = _fake_rca

    async def _no_checklist(l1, l2):
        return []
    rca_checklist.get_checklist = _no_checklist

    s = db.SessionLocal()
    try:
        out = asyncio.run(api.regenerate_rca(
            rid, api.ScenarioRegen(scenarios=[]), db=s))
        s.commit()
    finally:
        s.close()
    return out


def _reload(db, rid):
    s = db.SessionLocal()
    try:
        return s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
    finally:
        s.close()


# ── the validator has to actually run on this path ──────────────────────────

def test_regenerating_runs_the_validator_rather_than_storing_raw_output(app_env):
    """Every coercion below is one the validator makes. If the endpoint stores
    the model's dict untouched they all fail together, which is the point:
    a layer nothing calls is indistinguishable from one that works."""
    db, api = app_env
    rid = _seed(db)
    out = _regenerate(db, api, rid)
    v3 = out["rca_v3"]

    assert v3["l1"] == "Miscellaneous Issue", "an invented category was stored as given"
    assert v3["l1_raw"] == "Refund Issue", "the model's raw value must stay recoverable"
    assert v3["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] == "Unknown"
    assert v3["what_went_wrong"]["guest_issues"][0]["evidence"][0]["source"] == "booking"
    assert v3["issue_specific_answers"][0]["verdict"] == "Unknown"
    assert "28 minutes" in v3["issue_specific_answers"][0]["evidence"]
    assert v3["sop_compliance"]["verdict"] == "unknown"
    assert v3["takedown"]["verdict"] == "Untraceable"
    assert v3["booking_logs"][0]["time"] is None
    assert v3["flags"][0]["team"] == "OTHER"
    assert v3["flags"][0]["team_raw"] == "Growth"


def test_the_coercions_are_reported_not_applied_silently(app_env):
    db, api = app_env
    rid = _seed(db)
    out = _regenerate(db, api, rid)
    assert any("taxonomy" in n for n in out["validation_notes"])


# ── every v4 column is written, not just rca_v3 ─────────────────────────────

def test_the_v4_columns_are_written_on_regeneration(app_env):
    """Writing rca_v3 alone left these holding the previous run's answer —
    which reads as data, not as staleness."""
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    d = _reload(db, rid)

    assert d.guest_issues and d.guest_issues[0]["issue"] == "Voucher never delivered"
    assert d.sop_compliance["verdict"] == "unknown"
    assert d.booking_logs and d.booking_logs[0]["what"] == "Fulfilment attempted"
    assert d.takedown["verdict"] == "Untraceable"
    assert d.dss["prescribes"] == "Refund in full."
    assert d.flags[0]["flag"] == "No alert on failed fulfilment", \
        "the stale flag from the previous run is still there"


def test_the_reply_and_resolution_come_from_the_rca_now(app_env):
    """v4 emits both. Leaving them meant a fresh RCA sat next to a reply
    written against the previous one."""
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    d = _reload(db, rid)
    assert d.resolution == "Full refund of EUR 84."
    assert "refunded you in full" in d.suggested_response


def test_issue_specific_answers_are_stored_as_a_list(app_env):
    """v3 stored {question: answer}. A dict here renders nothing in v4.

    Both cases matter, and only the empty one exercises the default: an RCA
    with answers is a list either way, so a test that only covers that passes
    against the v3 fallback.
    """
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    assert isinstance(_reload(db, rid).issue_specific_answers, list)

    no_answers = dict(DIRTY_RCA, issue_specific_answers=[])
    _regenerate(db, api, rid, rca=no_answers)
    got = _reload(db, rid).issue_specific_answers
    assert got == [] and isinstance(got, list), \
        f"an RCA with no answers stored {got!r}, which the v4 renderer cannot read"


def test_regenerating_passes_the_approved_reply_voice(app_env, monkeypatch):
    """The reply now comes from the RCA call, so the tone reference has to
    reach it — on this path too, or a regenerated reply drifts out of the
    approved register while the pipeline's stays in it."""
    db, api = app_env
    canned = [{"situation": "Ticket delivered late", "response": "So sorry."}]

    async def _canned(l1, l2, sub_theme, text):
        return canned
    import server.services.canned as canned_mod
    monkeypatch.setattr(canned_mod, "get_canned_responses", _canned)

    rid, seen = _seed(db), {}
    _regenerate(db, api, rid, seen=seen)
    assert seen.get("canned_list") == canned, \
        "regenerate-rca calls the model with no voice reference"


def test_a_dead_canned_sheet_does_not_take_the_rca_down(app_env, monkeypatch):
    """The sheet 403s in this environment already. A tone reference is a nice
    to have; the RCA is not."""
    db, api = app_env

    async def _boom(*a, **k):
        raise RuntimeError("403 Forbidden")
    import server.services.canned as canned_mod
    monkeypatch.setattr(canned_mod, "get_canned_responses", _boom)

    rid, seen = _seed(db), {}
    out = _regenerate(db, api, rid, seen=seen)
    assert out["ok"] and seen.get("canned_list") == []


# ── the pipeline's own save, asserted at source ─────────────────────────────


def test_the_pipeline_passes_the_approved_reply_voice():
    i = PIPE.find("claude.generate_rca_v3(")
    assert "canned_list=canned_list" in PIPE[i:i + 1200], \
        "the pipeline generates a reply with no voice reference"
    assert "get_canned_responses(" in PIPE[max(0, i - 1200):i], \
        "canned_list is passed but never looked up"



def test_the_pipeline_validates_before_it_persists():
    assert "rca_v4_validate import validate" in PIPE, \
        "the pipeline stores the model's RCA without validating it"
    i, j = PIPE.find("_validate_rca(rca_v3"), PIPE.find("draft.rca_v3 ")
    assert 0 < i < j, "validation must run before the draft is written"


def test_the_pipeline_writes_every_v4_column():
    """A JSON column assigned but not flagged is invisible to SQLAlchemy on a
    re-run — the write happens and nothing reaches the database."""
    i = PIPE.find("for _col in (")
    assert i > 0
    flagged = PIPE[i:PIPE.find("):", i)]
    for col in ("guest_issues", "sop_compliance", "booking_logs",
                "flags", "takedown", "dss"):
        assert f"draft.{col} " in PIPE, f"the pipeline never writes {col}"
        assert f'"{col}"' in flagged, f"{col} is written but never flagged modified"


def test_the_pipeline_does_not_also_run_the_standalone_drafter():
    """Two replies were generated per review and the better-grounded one was
    thrown away, because _draft_dict reads the column the drafter wrote."""
    assert "claude.draft_response_v2(" not in PIPE
    assert 'rca_v3 or {}).get("suggested_response")' in PIPE


def test_a_validation_note_reaches_the_confidence_trail():
    """A coercion the reader cannot see is a silent edit."""
    i = PIPE.find("_validate_rca(rca_v3")
    assert "confidence_trail.append" in PIPE[i:i + 900]


def test_a_coercion_is_marked_warn_not_pass():
    """These sit in the same list as the pipeline's own step results. Marked
    pass, "we changed the model's answer" reads as "a step succeeded" — which
    is how a coerced enum becomes a trusted fact."""
    i = PIPE.find("_validate_rca(rca_v3")
    block = PIPE[i:i + 900]
    j = block.find("confidence_trail.append")
    assert '"mark": "warn"' in block[j:j + 200], \
        "a validator coercion is being reported as a successful step"


def test_every_path_that_produces_an_rca_validates_it():
    """A validator wired into one path looks exactly like one that works —
    which is the defect this whole layer was written to fix."""
    for src, name in ((PIPE, "server/pipeline.py"), (API, "server/api.py")):
        for i, _ in _find_all(src, "generate_rca_v3("):
            after = src[i:i + 3000]
            assert "_validate_rca(" in after, \
                f"{name}: a generate_rca_v3 call at offset {i} never validates its output"


def _find_all(hay: str, needle: str):
    i = hay.find(needle)
    while i >= 0:
        yield i, needle
        i = hay.find(needle, i + 1)
