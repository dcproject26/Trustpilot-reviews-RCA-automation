"""A run must not hold a DB connection across the model-call phase.

THE BUG. process_review opens one long-lived session and, after the early-match
persist commit, read `review` again — a lazy-load that checked out a connection
and held it, idle-in-transaction, across every model call below (stated_issue,
generate_rca_v2/v3, analyze_wwr, translate_outgoing). Neon drops a connection
left idle-in-transaction, so step 14's save-phase query died with
`PendingRollbackError: Can't reconnect until invalid transaction is rolled
back` — every run failing in the save phase, which is what Part B surfaced the
moment runs actually executed that far. (A second instance of the same class:
the previous Actions Taken were read on the run's session right before
translate_outgoing; that read now owns a short session — _prev_hand_typed_actions.)

Driven, not asserted against source: process_review is run for real against a
throwaway database, and the engine's pool is measured at each model call.
Single-threaded, so `checkedout()` reflects the run's own session. Zero means
the connection was released for the phase; without the fix it was one.
"""
import asyncio
import json
import sys

from tests.test_pipeline_validates_its_rca import _stub, BASE, _seed


def test_no_connection_is_held_across_any_model_call(live_db, monkeypatch):
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    pipe = sys.modules["server.pipeline"]
    from server.services import claude, reply_language

    held = {}

    def _watch(obj, name):
        orig = getattr(obj, name)

        async def _w(*a, **k):
            held[name] = max(held.get(name, 0), live_db.engine.pool.checkedout())
            return await orig(*a, **k)
        monkeypatch.setattr(obj, name, _w)

    for nm in ("stated_issue", "generate_rca_v2", "generate_rca_v3", "analyze_wwr"):
        _watch(claude, nm)

    async def _to(text, review, review_id):
        held["translate_outgoing"] = max(held.get("translate_outgoing", 0),
                                         live_db.engine.pool.checkedout())
        return (text, text, None, None)
    monkeypatch.setattr(reply_language, "translate_outgoing", _to)

    _seed(live_db, "tp_hold")
    asyncio.run(pipe.process_review("tp_hold"))

    assert held, "no model call was reached — the probe never ran"
    offenders = {k: v for k, v in held.items() if v != 0}
    assert not offenders, (
        f"the run held a DB connection across model call(s) {offenders} — "
        f"idle-in-transaction across a model round-trip is what Neon drops")


def test_the_run_still_completes_and_marks_the_review_a_draft(live_db, monkeypatch):
    """The connection is released by DETACHING review, so the guard is that the
    status write at save still lands: review is re-attached, not left detached
    (a detached write vanishes and the row stays 'new' — the Part A defect)."""
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    pipe = sys.modules["server.pipeline"]

    _seed(live_db, "tp_done")
    asyncio.run(pipe.process_review("tp_done"))

    s = live_db.SessionLocal()
    try:
        r = s.get(live_db.Review, "tp_done")
        d = s.query(live_db.RcaDraft).filter_by(review_id="tp_done").first()
    finally:
        s.close()
    assert r.status == "draft", "a finished run left the review status unset"
    assert d is not None and d.generated_at is not None, \
        "the run did not reach the save phase"


def test_prev_hand_typed_actions_reads_on_its_own_session(live_db, monkeypatch):
    """The previous Actions Taken are read without touching the run's session —
    the read owns a session it opens and closes, so no transaction is left open
    across translate_outgoing. Driven: seed a draft, call the function, and the
    caller's session (a fresh one here) is untouched and holds nothing after."""
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    pipe = sys.modules["server.pipeline"]

    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(id="d_prev", review_id="tp_prev",
                           actions_taken={"CO": ["we called the guest"]},
                           rca_v3={"what_went_wrong": {"gaps": []}}))
    s.commit()
    s.close()

    before = live_db.engine.pool.checkedout()
    actions, unattributed = pipe._prev_hand_typed_actions("tp_prev")
    after = live_db.engine.pool.checkedout()

    assert after == before, "the read leaked a checked-out connection"
    # and it read the seeded row: the hand-typed action survives, and with the
    # previous gaps stored (an empty list) it is attributable, not counted.
    assert actions == {"CO": ["we called the guest"]}
    assert unattributed == 0
