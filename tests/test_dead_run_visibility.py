"""Part A — a dead run must not look like a finished one, and a stale RCA must
not post.

A half-built RCA was indistinguishable from a finished one: a run that wrote its
early draft (at the match) and then died left the review `status:"new"` with a
draft row, and the inbox rendered it as a clean T1/T2 card. Separately, a
booking confirmed but never rebuilt (the rebuild is a fire-and-forget task that
dies on autoscale) left the old "we couldn't find your booking" reply postable —
that is how a wrong reply reached a public review page.

Driven, not asserted: processing_state and has_rca_to_post are called directly,
and the confirm→rebuild-dies→not-postable path is driven through the real
endpoints.
"""
from types import SimpleNamespace as NS

from server.tiers import processing_state, _recorded_run_failure
from server.api import has_rca_to_post


# ── A1: a drafted-but-unfinished run reports as dead, not blank ─────────────

def test_a_finished_review_reports_no_processing_state():
    # status flipped to draft/sent = the run finished; a clean card is correct.
    assert processing_state(NS(id="tp_done", status="draft"), NS(confidence_trail=[])) == ("", "")
    assert processing_state(NS(id="tp_sent", status="sent"), NS(confidence_trail=[])) == ("", "")


def test_a_drafted_but_unfinished_run_reports_as_dead(monkeypatch):
    # draft present, review still "new", nothing in progress → dead, and SAID.
    import server.pipeline as pipe
    monkeypatch.setattr(pipe, "PIPELINE_PROGRESS", {})   # nothing running here
    state, why = processing_state(NS(id="tp_dead", status="new"), NS(confidence_trail=[]))
    assert state == "stalled"
    assert why and "did not finish" in why, why


def test_an_in_flight_rerun_of_a_drafted_review_is_not_called_dead(monkeypatch):
    import time
    import server.pipeline as pipe
    monkeypatch.setattr(pipe, "PIPELINE_PROGRESS",
                        {"tp_live": {"updated_at": time.time(), "step": 3, "total": 8,
                                     "stage": "zendesk"}})
    state, _ = processing_state(NS(id="tp_live", status="new"), NS(confidence_trail=[]))
    assert state == "running", "an active re-run was reported as dead"


# ── A2: the recorded death is surfaced where the list looks ─────────────────

def test_a_recorded_run_failure_is_surfaced(monkeypatch):
    import server.pipeline as pipe
    monkeypatch.setattr(pipe, "PIPELINE_PROGRESS", {})
    d = NS(confidence_trail=[{"mark": "fail", "title": "Run failed — TimeoutError",
                              "text": "<strong>Run failed</strong> — the model timed out"}])
    state, why = processing_state(NS(id="tp_f", status="new"), d)
    assert state == "stalled"
    assert "the model timed out" in why and "<strong>" not in why, why


def test_the_partial_marker_is_surfaced(monkeypatch):
    import server.pipeline as pipe
    monkeypatch.setattr(pipe, "PIPELINE_PROGRESS", {})
    d = NS(confidence_trail=[{"mark": "warn",
                              "text": "<strong>This run has not finished</strong> — matching is done"}])
    state, why = processing_state(NS(id="tp_p", status="new"), d)
    assert state == "stalled" and "reached the match" in why, why


def test_recorded_run_failure_is_empty_when_nothing_was_recorded():
    assert _recorded_run_failure(NS(confidence_trail=[])) == ""
    assert _recorded_run_failure(NS(confidence_trail=None)) == ""


# ── A3: a stale RCA is not postable ─────────────────────────────────────────

def test_a_stale_rca_is_not_postable_even_with_content_or_override():
    d = NS(rca_stale=True, slack_thread_override="a hand-written reply",
           rca_v3={"what_went_wrong": {"guest_issues": [{"issue": "x"}]}}, l1="Ops")
    assert has_rca_to_post(d) is False


def test_a_fresh_rca_is_postable():
    d = NS(rca_stale=False, slack_thread_override="",
           rca_v3={"what_went_wrong": {"guest_issues": [{"issue": "x"}]}}, l1="Ops")
    assert has_rca_to_post(d) is True


def test_confirming_a_candidate_whose_rebuild_dies_is_not_postable(client, live_db, monkeypatch):
    """THE path that put a wrong reply on a public page: confirm a candidate,
    the rebuild dies, and the stale 'we couldn't find your booking' reply must
    NOT be postable — and the post endpoint must say why, not silently do it."""
    import server.pipeline as pipe
    import server.services.bigquery_patch as bqp
    monkeypatch.setattr(pipe, "run_batch_sync", lambda *a, **k: None)   # rebuild dies
    monkeypatch.setattr(bqp, "verify_bid", lambda bid: None)            # no warehouse

    s = live_db.SessionLocal()
    s.add(live_db.Review(id="tp_conf", rating=1, author="A", body_original="b",
                         body_english="b", status="draft", slack_channel="C1", slack_ts="1"))
    s.add(live_db.RcaDraft(id="d_conf", review_id="tp_conf", candidate_state=True,
                           candidates_list=[{"id": "999", "experience": "X"}],
                           rca_v3={"what_went_wrong": {"guest_issues": [
                               {"issue": "we couldn't find your booking"}]}},
                           rca_stale=False))
    s.commit()
    s.close()

    r = client.post("/api/reviews/tp_conf/select-candidate", json={"bid": "999"})
    assert r.status_code == 200, r.text

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_conf").first()
    assert d.rca_stale is True, "confirming a candidate did not mark the RCA stale"
    assert has_rca_to_post(d) is False, "a stale RCA is still postable"
    s.close()

    p = client.post("/api/reviews/tp_conf/post-rca").json()
    assert p["posted"] is False, "a stale reply was posted"
    assert "rebuilt" in p["why"] or "previous match" in p["why"], p["why"]


def test_a_completed_run_clears_the_stale_flag(monkeypatch):
    """The other half of A3: a confirmation sets rca_stale, and a run that
    FINISHES rebuilding must clear it — or a confirmed review could never become
    postable again. Driven through a real process_review to the final save."""
    import asyncio
    import importlib
    import json
    import tempfile
    from datetime import datetime

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

    s = db.SessionLocal()
    s.add(db.Review(id="tp_clear", slack_ts="tp_clear", slack_channel="C1", rating=1,
                    author="A", body_original="x", body_english="x", status="new",
                    received_at=datetime.utcnow()))
    s.add(db.RcaDraft(id="d_clear", review_id="tp_clear", rca_stale=True))
    s.commit()
    s.close()

    asyncio.run(pipe.process_review("tp_clear"))

    s = db.SessionLocal()
    stale = s.query(db.RcaDraft).filter_by(review_id="tp_clear").first().rca_stale
    s.close()
    from tests.conftest import drop_temp_db
    drop_temp_db(tmp.name)
    assert stale is False, "a completed run did not clear rca_stale"
