"""The Reporting self-check: does it actually catch a broken number, and does it
tell a structural zero apart from a measured one?

A checker that passes everything is indistinguishable from one that never ran, so
most of these tests FEED IT BROKEN DATA and require it to object.
"""
from datetime import datetime

import pytest

from server.services import reporting_selfcheck as sc
from server.services.reporting_selfcheck import (
    invariants, data_health, dimension_fill, run)


def _rec(**kw):
    base = dict(review_id="r", date="2027-06-01", rating=1, language="English",
                status="sent", experience=None, vendor=None, tgid=None,
                fulfilment_type=None, booking_status=None, tier="Tier 1",
                traceable="Traceable", match_method="none", l1="Operations Issue",
                l2="Ticket Issues", sub_themes=[], scenarios=[],
                overlay_scenarios=[], claim_accuracy=[], resolution_given="No",
                takedown=None, outcome_category=None, dss_followed=None,
                owner="Avi", sent_route=None, close_reason=None, prompt_version=None,
                solved=True, posted=True, issue_count=2, zd_count=1, flag_count=1,
                tts_hours=10.0)
    base.update(kw)
    return base


# ── the invariants pass on sound data ───────────────────────────────────────

def test_sound_data_passes_every_invariant():
    recs = [_rec(tier="Tier 1"), _rec(tier="Tier 2"), _rec(tier="Untraceable")]
    results = invariants(recs)
    assert results, "the checker produced no checks at all"
    failed = [r for r in results if not r["ok"]]
    assert not failed, f"sound data failed: {failed}"
    # every check states what it compared, so a pass is evidence, not a tick
    assert all(r["detail"] for r in results)


# ── ...and catch data that is NOT sound ─────────────────────────────────────

def test_a_percentage_outside_0_to_100_is_caught():
    """The "130%" class of bug: a share that exceeds its own denominator."""
    def broken(rs):
        return 130.0
    original = sc.MEASURES["solved_pct"]
    sc.MEASURES["solved_pct"] = (original[0], broken)
    try:
        failed = [r for r in invariants([_rec()]) if not r["ok"]]
    finally:
        sc.MEASURES["solved_pct"] = original
    assert any("percentage" in r["check"].lower() for r in failed), (
        f"a 130% share was not caught: {failed}")


def test_a_dimension_that_loses_rows_is_caught():
    """If a grouping drops a review, the buckets stop summing to the cohort."""
    original = sc.run_query

    def lossy(recs, dims, meas, **kw):
        out = original(recs, dims, meas, **kw)
        if dims == ["tier"] and out["rows"]:
            out["rows"] = out["rows"][:-1]          # silently lose a bucket
        return out
    sc.run_query = lossy
    try:
        failed = [r for r in invariants([_rec(tier="Tier 1"), _rec(tier="Tier 2")])
                  if not r["ok"]]
    finally:
        sc.run_query = original
    assert any("sum to the cohort" in r["check"] for r in failed), (
        f"a lost bucket was not caught: {failed}")


def test_a_solved_review_with_no_finish_timestamp_is_caught():
    # "Solved" and "median time to send" would then describe different people.
    failed = [r for r in invariants([_rec(solved=True, tts_hours=None)])
              if not r["ok"]]
    assert any("finish timestamp" in r["check"] for r in failed), failed


# ── structural zero vs measured zero ────────────────────────────────────────

def test_an_empty_source_field_is_reported_as_empty_not_zero():
    """THE POINT. Every review has flag_count 0, so "Flags raised" renders 0 —
    but that is the data being absent, not an answer of zero."""
    recs = [_rec(flag_count=0, zd_count=0) for _ in range(5)]
    health = {h["measure"]: h for h in data_health(recs)}
    assert health["flags"]["state"] == "empty"
    assert "not because the answer is zero" in health["flags"]["note"]
    assert health["zendesk"]["state"] == "empty"
    # ...while a field everyone carries is not flagged
    assert health["count"]["state"] == "ok"


def test_a_partially_filled_measure_says_how_many_are_missing():
    recs = [_rec(posted=True), _rec(posted=False), _rec(posted=False)]
    health = {h["measure"]: h for h in data_health(recs)}
    assert health["posted"]["state"] == "partial"
    assert health["posted"]["have"] == 1 and health["posted"]["of"] == 3
    assert "2 of 3" in health["posted"]["note"]


def test_a_fully_populated_measure_is_ok():
    health = {h["measure"]: h for h in data_health([_rec(), _rec()])}
    assert health["solved"]["state"] == "ok"
    assert health["median_tts"]["state"] == "ok"


def test_dimension_fill_reports_absence():
    recs = [_rec(vendor="Acme"), _rec(vendor=None), _rec(vendor=None)]
    fill = {d["key"]: d for d in dimension_fill(recs)}
    assert fill["vendor"]["have"] == 1 and fill["vendor"]["of"] == 3
    assert fill["vendor"]["state"] == "partial"
    assert fill["outcome_category"]["state"] == "empty"
    assert fill["tier"]["state"] == "ok"


# ── the verdict distinguishes the three outcomes ────────────────────────────

def test_verdict_separates_a_bug_from_absent_data_from_a_clean_run(live_db):
    from server.db import Review, RcaDraft
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="sc1", received_at=datetime(2027, 6, 1, 9), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="sc1-d", review_id="sc1", match_tier=1,
                       booking={"id": "B1"}, sent_at=datetime(2027, 6, 1, 15)))
        s.commit()
        out = run(s, datetime(2027, 6, 1), datetime(2027, 6, 2))
    finally:
        s.close()
    assert out["reviews"] == 1
    assert out["invariants_failed"] == 0
    # this review has no flags/zendesk, so the verdict must SAY the measures are
    # unreadable rather than claim everything is fine
    assert "no data behind them" in out["verdict"]
    empties = [m["measure"] for m in out["measures"] if m["state"] == "empty"]
    assert "flags" in empties


def test_an_empty_window_says_so_rather_than_passing_vacuously():
    """No rows must not read as "all checks passed" — that is the ran-and-found-
    nothing trap: a vacuous green is indistinguishable from a real one."""
    health = data_health([])
    # NOT "ok": with nothing to measure, "have == total" is 0 == 0 and every
    # measure would otherwise report healthy — a green that means nothing.
    assert health, "an empty window produced no verdict at all"
    assert all(h["state"] == "unknown" for h in health),         f"an empty window reported a confident state: {health[:2]}"
    assert all("nothing to judge" in h["note"] for h in health)


def test_selfcheck_endpoint_reports_over_the_window(client):
    from server.db import Review, RcaDraft, SessionLocal
    s = SessionLocal()
    try:
        s.add(Review(id="sce1", received_at=datetime(2027, 7, 2, 9), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="sce1-d", review_id="sce1", match_tier=2,
                       booking={"id": "B9"}, sent_at=datetime(2027, 7, 2, 12)))
        s.commit()
    finally:
        s.close()
    r = client.get("/api/reporting/selfcheck?date_from=2027-07-02&date_to=2027-07-02")
    assert r.status_code == 200
    d = r.json()
    assert d["reviews"] == 1
    assert d["invariants_failed"] == 0
    assert any(c["check"] for c in d["invariants"])
    assert any(m["state"] == "empty" for m in d["measures"])


def test_selfcheck_rejects_a_bad_date(client):
    r = client.get("/api/reporting/selfcheck?date_from=02-07-2027")
    assert r.status_code == 422 and "YYYY-MM-DD" in r.json()["detail"]
