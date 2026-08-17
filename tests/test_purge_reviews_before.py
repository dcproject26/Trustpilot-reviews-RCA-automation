"""Deleting reviews before a boundary must take their dependents with them.

There is no other delete path in this codebase and nothing here is undoable from
inside the app, so the parts that decide WHAT goes are driven directly rather
than trusted: the boundary lookup, the selection, and the cascade.

An orphan is the quiet failure here. Deleting only `reviews` leaves rca_drafts,
review_metrics and run_jobs rows that no screen shows and no query cleans up —
and an orphaned queued run_job has the drain loop reaching for a review that no
longer exists.
"""
from datetime import datetime

import pytest

from tools.purge_reviews_before import _boundary, collect, purge


def _seed(live_db, rid, when, status="draft", author="A"):
    s = live_db.SessionLocal()
    try:
        s.add(live_db.Review(id=rid, rating=1, author=author,
                             body_original="b", status=status,
                             received_at=when))
        s.add(live_db.RcaDraft(id=f"draft_{rid}", review_id=rid, booking={}))
        s.add(live_db.ReviewMetric(review_id=rid, received_at=when,
                                   channel="C", rating=1, language="en"))
        s.add(live_db.RunJob(id=f"job_{rid}", review_id=rid, reason="re-run",
                             status="queued", attempts=0,
                             created_at=when, updated_at=when))
        s.commit()
    finally:
        s.close()


@pytest.fixture()
def seeded(live_db):
    _seed(live_db, "tp_old1", datetime(2026, 8, 10, 9, 0))
    _seed(live_db, "tp_old2", datetime(2026, 8, 14, 9, 0), status="sent")
    _seed(live_db, "tp_edge", datetime(2026, 8, 15, 10, 44, 49))
    _seed(live_db, "tp_new1", datetime(2026, 8, 15, 20, 0))
    return live_db


# ── the boundary ───────────────────────────────────────────────────────────

def test_an_unknown_boundary_refuses_and_says_what_to_do(seeded):
    s = seeded.SessionLocal()
    try:
        row, why = _boundary(s, seeded, "tp_does_not_exist")
        assert row is None
        assert "no review" in why
        assert "/api/reviews" in why, "the refusal does not say how to find one"
    finally:
        s.close()


def test_a_boundary_with_no_date_refuses_rather_than_guessing(seeded):
    s = seeded.SessionLocal()
    try:
        s.add(seeded.Review(id="tp_undated", rating=1, body_original="b",
                            status="new", received_at=None))
        s.commit()
        row, why = _boundary(s, seeded, "tp_undated")
        assert row is None
        assert "received_at" in why
    finally:
        s.close()


# ── the selection ──────────────────────────────────────────────────────────

def test_the_boundary_review_itself_is_kept(seeded):
    """STRICTLY before. Deleting the row the caller named as the edge is the
    off-by-one nobody notices until the review they were protecting is gone."""
    s = seeded.SessionLocal()
    try:
        edge, _ = _boundary(s, seeded, "tp_edge")
        ids = [r.id for r in collect(s, seeded, edge.received_at)]
        assert "tp_edge" not in ids
        assert ids == ["tp_old1", "tp_old2"]
    finally:
        s.close()


def test_nothing_after_the_boundary_is_selected(seeded):
    s = seeded.SessionLocal()
    try:
        edge, _ = _boundary(s, seeded, "tp_edge")
        assert "tp_new1" not in [r.id for r in collect(s, seeded, edge.received_at)]
    finally:
        s.close()


def test_an_undated_review_is_never_swept_up(seeded):
    """A NULL received_at is not "before" anything — comparing it would be a
    guess, and this is the one operation where a guess is unrecoverable."""
    s = seeded.SessionLocal()
    try:
        s.add(seeded.Review(id="tp_undated", rating=1, body_original="b",
                            status="new", received_at=None))
        s.commit()
        edge, _ = _boundary(s, seeded, "tp_edge")
        assert "tp_undated" not in [r.id for r in collect(s, seeded, edge.received_at)]
    finally:
        s.close()


# ── the cascade ────────────────────────────────────────────────────────────

def test_dependents_go_with_the_review(seeded):
    """THE ORPHAN TEST. Deleting only `reviews` leaves rows nothing shows."""
    s = seeded.SessionLocal()
    try:
        counts = purge(s, seeded, ["tp_old1", "tp_old2"])
        assert counts == {"reviews": 2, "drafts": 2, "metrics": 2, "jobs": 2}
        assert s.query(seeded.RcaDraft).filter(
            seeded.RcaDraft.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
        assert s.query(seeded.ReviewMetric).filter(
            seeded.ReviewMetric.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
        assert s.query(seeded.RunJob).filter(
            seeded.RunJob.review_id.in_(["tp_old1", "tp_old2"])).count() == 0
    finally:
        s.close()


def test_the_survivors_are_untouched(seeded):
    s = seeded.SessionLocal()
    try:
        purge(s, seeded, ["tp_old1", "tp_old2"])
        left = {r.id for r in s.query(seeded.Review).all()}
        assert left == {"tp_edge", "tp_new1"}
        # and their dependents are still there
        assert s.query(seeded.RcaDraft).filter(
            seeded.RcaDraft.review_id == "tp_edge").first() is not None
        assert s.query(seeded.RunJob).filter(
            seeded.RunJob.review_id == "tp_new1").first() is not None
    finally:
        s.close()


def test_purging_nothing_deletes_nothing(seeded):
    s = seeded.SessionLocal()
    try:
        assert purge(s, seeded, []) == {"reviews": 0, "drafts": 0,
                                        "metrics": 0, "jobs": 0}
        assert s.query(seeded.Review).count() == 4
    finally:
        s.close()


def test_the_refusal_names_the_database_it_looked_in(seeded):
    """THE FAILURE THIS FILE'S OWN SCRIPT HAD. "no review X" is the same
    sentence for an id that is wrong and one that lives in the OTHER database
    — and this project runs Development beside Production, so "wrong database"
    is the likelier of the two. Run in the dev repl against a production-only
    review, the honest answer names where it looked.
    """
    s = seeded.SessionLocal()
    try:
        _, why = _boundary(s, seeded, "tp_only_in_production")
        assert "sqlite" in why or "postgres" in why, why      # it says WHERE
        assert "4 review(s)" in why, why                      # and how many are there
        assert "DATABASE_URL" in why, why                     # and how to point elsewhere
    finally:
        s.close()
