"""Posting the RCA to the thread must be able to finish the review.

THE GAP. `/post-rca` sets `rca_posted_at` and nothing else, so a matched review
whose RCA had gone to the Slack thread stayed in Matched. Only `/send` sets
`status = "sent"`, and the Send ↑ button lives in the RCA column header — while
the work happens in the Slack-post block at the bottom of the column. The
review was finished and there was no way to put it down without hunting for a
button somewhere else.

ONE ENDPOINT, NOT TWO. The new control calls `/send`, the same endpoint Send ↑
calls. Two code paths to one outcome is how the RCA got posted into a thread
twice in the first place — `/send` already refuses to post when
`rca_posted_at` is set, and that guard is REUSED rather than re-implemented.

THE ROUTE IS DERIVED, NOT DECLARED. `sent_route` is worked out server-side from
whether the RCA was already in the thread when `/send` was called. A route the
client asserts is a route that can be wrong, and the Sent tab is the one place
that must not be.

THREE OUTCOMES, THREE VALUES. A review whose reply and RCA both went out
(`reply`), one closed out with nothing to send (`closed`), and one whose RCA
was posted and then marked finished (`rca_posted`). Plus `no_rca` for a send
with no analysis to post, which is its own outcome and not a kind of `reply` —
merging them is the silent-zero bug wearing a status field.
"""
import importlib
import os
import tempfile
from datetime import datetime

import pytest
from tests.conftest import drop_temp_db


@pytest.fixture()
def env(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    monkeypatch.setenv("MOCK_MODE", "true")
    import server.config as cfg; importlib.reload(cfg)
    import server.db as db; importlib.reload(db); db.init_db()
    import server.api as api; importlib.reload(api)
    import server.main as main; importlib.reload(main)
    from fastapi.testclient import TestClient
    yield TestClient(main.app), db, api
    drop_temp_db(tmp.name)


RCA = {"l1": "Operations Issue", "stated_issue": "Tickets were late.",
       "what_went_wrong": {"guest_issues": [{"issue": "Late tickets"}]}}


def _seed(db, rid="tp_m", posted=False, rca=True):
    s = db.SessionLocal()
    try:
        # slack_ts is unique — derived from the id so a test seeding several
        # reviews does not collide on it.
        s.add(db.Review(id=rid, slack_ts=f"ts_{rid}", slack_channel="C1", rating=1,
                        author="D", body_original="x", status="draft",
                        received_at=datetime.utcnow()))
        s.add(db.RcaDraft(id="d_" + rid, review_id=rid,
                          rca_v3=(RCA if rca else {}),
                          l1=("Operations Issue" if rca else None),
                          rca_posted_at=(datetime.utcnow() if posted else None)))
        s.commit()
    finally:
        s.close()
    return rid


def test_the_column_exists_on_a_fresh_database(env):
    """NOT BUILT guard. Without the column every route assertion below would
    fail on an AttributeError rather than on the behaviour."""
    _, db, _ = env
    assert hasattr(db.Review, "sent_route")


def test_marking_sent_after_a_post_does_not_post_again(env):
    """The whole point. The second copy in the thread is what people see."""
    c, db, _ = env
    rid = _seed(db, posted=True)
    out = c.post(f"/api/reviews/{rid}/send").json()
    assert out["posted"] is False, (
        "marking sent posted the RCA a second time — the guard did not hold")
    assert out["why"] == "already posted to the thread", out
    assert out["sent_route"] == "rca_posted", out


def test_it_actually_moves_the_review_to_sent(env):
    c, db, _ = env
    rid = _seed(db, posted=True)
    c.post(f"/api/reviews/{rid}/send")
    s = db.SessionLocal()
    try:
        r = s.query(db.Review).filter(db.Review.id == rid).first()
        assert r.status == "sent", (
            "the RCA is in the thread and the review is still in Matched — "
            "which is the reported gap, unchanged")
        assert r.sent_route == "rca_posted"
        assert r.closed_at is None, "marking sent is not closing out"
    finally:
        s.close()


def test_a_send_that_posts_the_rca_is_a_different_route(env):
    """Send ↑ on a review whose RCA has NOT been posted still posts it, and
    that is a different outcome from marking sent after posting."""
    c, db, _ = env
    rid = _seed(db, posted=False)
    out = c.post(f"/api/reviews/{rid}/send").json()
    assert out["sent_route"] == "reply", out
    s = db.SessionLocal()
    try:
        assert s.query(db.Review).filter(
            db.Review.id == rid).first().sent_route == "reply"
    finally:
        s.close()


def test_a_send_with_no_rca_is_its_own_route(env):
    """Not a kind of `reply`. A review sent with nothing to post is a
    different piece of work, and folding it in would make the Sent tab a count
    of three things wearing one label."""
    c, db, _ = env
    rid = _seed(db, posted=False, rca=False)
    out = c.post(f"/api/reviews/{rid}/send").json()
    assert out["sent_route"] == "no_rca", out
    assert out["posted"] is False


def test_closing_out_records_its_own_route(env):
    c, db, _ = env
    rid = _seed(db, posted=False, rca=False)
    c.post(f"/api/reviews/{rid}/close", json={"reason": "nothing to send"})
    s = db.SessionLocal()
    try:
        r = s.query(db.Review).filter(db.Review.id == rid).first()
        assert r.sent_route == "closed", r.sent_route
        assert r.closed_at is not None
    finally:
        s.close()


def test_the_four_routes_are_four_distinct_values(env):
    """The Sent tab has to be able to tell them apart, which it cannot do if
    two of them store the same string."""
    c, db, _ = env
    routes = {}
    for rid, posted, rca, close in (("tp_a", True, True, False),
                                    ("tp_b", False, True, False),
                                    ("tp_c", False, False, False),
                                    ("tp_d", False, False, True)):
        _seed(db, rid, posted=posted, rca=rca)
        if close:
            c.post(f"/api/reviews/{rid}/close")
        else:
            c.post(f"/api/reviews/{rid}/send")
        s = db.SessionLocal()
        try:
            routes[rid] = s.query(db.Review).filter(
                db.Review.id == rid).first().sent_route
        finally:
            s.close()
    assert len(set(routes.values())) == 4, (
        f"two routes to Sent store the same value: {routes}")


def test_the_route_reaches_the_inbox_payload(env):
    """The wire. A value written and never served is a value the tab cannot
    render — this project's most repeated bug."""
    c, db, _ = env
    rid = _seed(db, posted=True)
    c.post(f"/api/reviews/{rid}/send")
    rows = c.get("/api/reviews").json()
    row = [x for x in rows if x["id"] == rid]
    assert row, "the review vanished from the inbox after being sent"
    assert row[0].get("sent_route") == "rca_posted", row[0]


def test_the_route_reaches_the_single_review_payload(env):
    c, db, _ = env
    rid = _seed(db, posted=True)
    c.post(f"/api/reviews/{rid}/send")
    got = c.get(f"/api/reviews/{rid}").json()
    assert got["review"]["sent_route"] == "rca_posted", got["review"]


def test_sending_twice_does_not_change_the_route(env):
    """Idempotent in the way that matters: the second call still must not
    post, and must not relabel a review that was already finished."""
    c, db, _ = env
    rid = _seed(db, posted=True)
    first = c.post(f"/api/reviews/{rid}/send").json()
    second = c.post(f"/api/reviews/{rid}/send").json()
    assert first["sent_route"] == second["sent_route"] == "rca_posted"
    assert second["posted"] is False
