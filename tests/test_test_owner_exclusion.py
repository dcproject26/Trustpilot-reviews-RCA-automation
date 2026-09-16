"""Test-owner rows are excluded from EVERY reporting measure, in ONE place.

The rule: a review whose picked_up_by names a test account (TEST_OWNERS) is
non-production and does not count anywhere the digest or the Reporting page
computes a number — not in received, not in solved, not in pending, not in
the tier mix, not in the Reporting query's totals or groups or filters. The
dashboard is still allowed to show those rows; the exclusion applies only to
the analytics, and `selfcheck.excluded_test_rows` names how many were hidden
so the count is auditable.

These tests drive the DB layer so we catch a filter that was removed from one
call site but left on the others — the exact "one file was patched, the other
was not" failure this exclusion is meant to prevent.
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.services.daily_report import (
    IST, _db_bounds, pending_count, _received_between,
    _collect_solved_between,
    collect_pending_rows, build_daily_digest,
)
from server.services.reporting_query import records
from server.services.reporting_selfcheck import _excluded_test_rows, run


NOW  = datetime(2026, 9, 11, 14, 30, tzinfo=timezone.utc)   # 8pm IST run
START, END = _db_bounds(NOW)


def _n(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


def _plant(db, *, real: int, test: int, now: datetime = NOW):
    """`real` real reviews and `test` test-owner reviews, all inside the day
    the digest covers. Both cohorts arrive, are solved and carry a Tier so we
    can watch every measure at once."""
    from server.db import Review, RcaDraft
    s = db.SessionLocal()
    try:
        mid = START + timedelta(hours=6)
        for i in range(real):
            rid = f"real_{i}"
            s.add(Review(id=rid, received_at=_n(mid), status="sent", rating=1,
                         picked_up_by="Avi"))
            s.add(RcaDraft(id=rid + "-d", review_id=rid, match_tier=1,
                           sent_at=_n(mid), booking={"id": f"B{i}"}))
        for i in range(test):
            rid = f"test_{i}"
            s.add(Review(id=rid, received_at=_n(mid), status="sent", rating=1,
                         picked_up_by="Test"))
            s.add(RcaDraft(id=rid + "-d", review_id=rid, match_tier=2,
                           sent_at=_n(mid), booking={"id": f"BT{i}"}))
        s.commit()
    finally:
        s.close()


# ── the DB helpers, one by one, driven on the live schema ──────────────────

def test_received_between_drops_the_test_row(live_db):
    _plant(live_db, real=3, test=2)
    with live_db.SessionLocal() as s:
        # 3, not 5 — the two "Test" reviews arrived in this window and are
        # excluded. This is the entry point weekly_report also calls, so a
        # regression here breaks both digests at once.
        assert _received_between(s, START, END) == 3


def test_collect_solved_between_drops_the_test_row(live_db):
    _plant(live_db, real=3, test=2)
    with live_db.SessionLocal() as s:
        solved = _collect_solved_between(s, START, END)
    assert len(solved) == 3
    # Only real owners in the collected cohort — Test would slip in silently
    # if the filter had been left off the solved query.
    assert all(r.picked_up_by == "Avi" for r in solved)


def test_pending_count_drops_the_test_row(live_db):
    """A test row left open would inflate the backlog headline the manager
    reads as "how much is outstanding"."""
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="real_open",  received_at=_n(START), status="new",
                     rating=1, picked_up_by="Avi"))
        s.add(Review(id="test_open",  received_at=_n(START), status="new",
                     rating=1, picked_up_by="Test"))
        s.commit()
        assert pending_count(s) == 1                    # 1, not 2
    finally:
        s.close()



def test_collect_pending_rows_drops_the_test_row(live_db):
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="rp", received_at=_n(START), status="new",
                     rating=1, picked_up_by="Avi"))
        s.add(Review(id="tp", received_at=_n(START), status="new",
                     rating=1, picked_up_by="Test"))
        s.commit()
        rows = collect_pending_rows(s)
    finally:
        s.close()
    assert len(rows) == 1


def test_reporting_records_drops_the_test_row(live_db):
    _plant(live_db, real=3, test=2)
    with live_db.SessionLocal() as s:
        recs = records(s, START, END)
    assert len(recs) == 3
    # Owner column carries only production names — no test row leaked through
    # under a different projection.
    assert all(r["owner"] == "Avi" for r in recs)


# ── the digest as a whole, so no downstream builder can smuggle a test row in ──

def test_build_daily_digest_reflects_only_production_reviews(live_db):
    _plant(live_db, real=3, test=2)
    with live_db.SessionLocal() as s:
        text = build_daily_digest(s, NOW)
    # Received 3 (not 5), Solved 3 (not 5), all Tier 1 (test's Tier 2 gone).
    assert "• Received today — *3*" in text
    assert "• Solved today — *3*" in text
    assert "🟢 Tier 1 — 3" in text and "🟡 Tier 2 — 0" in text
    # "Test" must not appear as a person credited with anything.
    assert "Test" not in text


# ── selfcheck names the exclusion, so it is visible rather than silent ────

def test_selfcheck_reports_the_excluded_test_row_count(live_db):
    _plant(live_db, real=3, test=2)
    with live_db.SessionLocal() as s:
        d = run(s, START.replace(tzinfo=None), END.replace(tzinfo=None))
    assert d["reviews"] == 3
    # "5 on the dashboard, 3 in the digest, 2 excluded as test" is the exact
    # sum a manager needs to reconcile the two views. A silent exclusion is
    # what makes the numbers look wrong.
    assert d["excluded_test_rows"] == 2


def test_excluded_test_rows_only_matches_the_test_names(live_db):
    """Only picked_up_by is used as the signal, and only the four names.
    Matching on author or body would silently drop real complaints that
    happen to say "test" in them."""
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="x1", received_at=_n(START), status="new",
                     rating=1, picked_up_by="Test"))       # matches
        s.add(Review(id="x2", received_at=_n(START), status="new",
                     rating=1, picked_up_by=" QA "))       # matches (trimmed)
        s.add(Review(id="x3", received_at=_n(START), status="new",
                     rating=1, picked_up_by="Testator"))   # no substring match
        s.add(Review(id="x4", author="testing my patience",
                     received_at=_n(START), status="new",
                     rating=1, picked_up_by="Avi"))        # author-based never counts
        s.commit()
        assert _excluded_test_rows(s, START.replace(tzinfo=None),
                                      END.replace(tzinfo=None)) == 2
    finally:
        s.close()


def test_dashboard_parity_reconciles_exactly(live_db):
    """The verifier the team asked for: dashboard total = reporting total +
    test-owner rows, all-time, exactly. An unexplained gap is a hard fail."""
    from server.db import Review
    from server.services.reporting_selfcheck import dashboard_parity
    s = live_db.SessionLocal()
    try:
        for i in range(5):
            s.add(Review(id=f"real_{i}", received_at=_n(START), status="new",
                         rating=1, picked_up_by="Avi"))
        for i in range(2):
            s.add(Review(id=f"test_{i}", received_at=_n(START), status="new",
                         rating=1, picked_up_by="Test"))
        s.commit()
        p = dashboard_parity(s)
    finally:
        s.close()
    assert p["ok"] is True
    assert p["dashboard_total"] == 7
    assert p["reporting_total"] == 5
    assert p["excluded_test_rows"] == 2
    assert p["unexplained_gap"] == 0


def test_reconcile_flags_an_unexplained_gap():
    """The pure arithmetic behind the parity check. A gap that is NOT the
    test-owner rows must read not-ok — that is the whole point of the verifier."""
    from server.services.reporting_selfcheck import _reconcile
    ok = _reconcile(dashboard_total=7, reporting_total=5, excluded=2)
    assert ok["ok"] is True and ok["unexplained_gap"] == 0
    gap = _reconcile(dashboard_total=10, reporting_total=5, excluded=2)
    assert gap["ok"] is False
    assert gap["unexplained_gap"] == 3
    assert "UNEXPLAINED" in gap["note"]
