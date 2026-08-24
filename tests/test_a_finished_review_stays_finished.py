"""A review closed or sent while a run is in flight must stay that way.

THE REPORTED BUG. Reviews that had been closed out, or sent, reverted into a
working tab — and the reason they were closed went with them.

An RCA run is the longest window in this system: minutes of model calls. The
pipeline reads the Review row at the start, DETACHES it to release the database
connection for that phase, and at the end re-attached it with `db.merge(review)`
so the final status write would be tracked. merge() writes the WHOLE object
back, every column, with the values read minutes earlier — so anything a person
did to the review in the meantime was reverted on the run's way out:

    associate closes it      status=sent, sent_route=closed, closed_at,
                             close_reason all set
    the in-flight run ends   merge puts back status=new, sent_route=None,
                             closed_at=None, close_reason=None

The `if review.status != "sent"` guard could not catch it, because it tested the
STALE object's status, not the row's. This drives the real pipeline with the
close happening mid-run, which is the only way the stale window opens at all.
"""
import asyncio
import importlib
from datetime import datetime

import pytest


def _seed(db, rid):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="7.1", slack_channel="C_ORM", rating=1,
                        author="Ana", body_original="the venue was shut",
                        body_english="the venue was shut", status="new",
                        received_at=datetime(2026, 8, 20)))
        s.commit()
    finally:
        s.close()


def _close_out(db, rid):
    """What POST /api/reviews/{id}/close does, in its own session — an
    associate finishing the card while the run is still going."""
    s = db.SessionLocal()
    try:
        r = s.query(db.Review).filter(db.Review.id == rid).first()
        r.status = "sent"
        r.closed_at = datetime.utcnow()
        r.close_reason = "Finished from the RCA card without posting to Slack."
        r.sent_route = "closed"
        s.commit()
    finally:
        s.close()


def _row(db, rid):
    s = db.SessionLocal()
    try:
        return s.query(db.Review).filter(db.Review.id == rid).first()
    finally:
        s.close()


def _run_with_close_midway(live_db, monkeypatch, rid):
    """Run the pipeline, closing the review from another session partway
    through — inside a step that happens AFTER the review is detached."""
    import server.pipeline as P
    importlib.reload(P)
    monkeypatch.setattr(P, "is_live", lambda svc: False)
    monkeypatch.setattr(P, "MOCK_MODE", False)

    closed = {"done": False}

    async def _close_then_return(*a, **kw):
        if not closed["done"]:
            _close_out(live_db, rid)
            closed["done"] = True
        return {}
    # A late step, well past the detach: the associate presses Close while the
    # model half is still running.
    monkeypatch.setattr(P.claude, "generate_rca_v3", _close_then_return)

    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass
    assert closed["done"], "the close never happened — this test proves nothing"


def test_a_close_during_a_run_is_not_reverted(live_db, monkeypatch):
    rid = "tp_close_midrun"
    _seed(live_db, rid)
    _run_with_close_midway(live_db, monkeypatch, rid)

    r = _row(live_db, rid)
    assert r.status == "sent", (
        f"the finished review was pulled back into a working tab "
        f"(status={r.status!r}) by a run that started before it was closed")


def test_the_reason_it_was_closed_survives_too(live_db, monkeypatch):
    """status alone is not the bug. merge() reverted every column, so the
    review came back with no record of why it had been finished — and a review
    in Sent with no close reason is indistinguishable from one that was never
    closed at all."""
    rid = "tp_close_reason"
    _seed(live_db, rid)
    _run_with_close_midway(live_db, monkeypatch, rid)

    r = _row(live_db, rid)
    assert r.sent_route == "closed", f"sent_route={r.sent_route!r}"
    assert r.closed_at is not None, "closed_at was wiped"
    assert r.close_reason and "without posting" in r.close_reason, \
        f"close_reason={r.close_reason!r}"


def test_the_review_still_reads_as_sent_in_the_inbox(live_db, monkeypatch):
    """The consequence the associate actually sees: which tab it lands in."""
    from server.tiers import classify
    rid = "tp_close_bucket"
    _seed(live_db, rid)
    _run_with_close_midway(live_db, monkeypatch, rid)

    s = live_db.SessionLocal()
    try:
        r = s.query(live_db.Review).filter(live_db.Review.id == rid).first()
        assert classify(r, r.draft) == "sent", \
            "the closed review renders under a working tab again"
    finally:
        s.close()


def test_an_untouched_review_still_becomes_a_draft(live_db, monkeypatch):
    """The other half, which the fix must not break: a run that nobody
    interfered with still flips the review out of 'new'. A finished run
    reading as unstarted is the defect the merge was there for."""
    import server.pipeline as P
    importlib.reload(P)
    monkeypatch.setattr(P, "is_live", lambda svc: False)
    monkeypatch.setattr(P, "MOCK_MODE", False)

    async def _noop(*a, **kw):
        return {}
    monkeypatch.setattr(P.claude, "generate_rca_v3", _noop)

    rid = "tp_untouched"
    _seed(live_db, rid)
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass
    assert _row(live_db, rid).status == "draft", \
        "a finished run left the review reading as unstarted"


def test_a_review_deleted_mid_run_is_not_resurrected(live_db, monkeypatch):
    """A purge during a run. merge() would re-INSERT the row from the stale
    copy, bringing back a review someone deliberately removed."""
    import server.pipeline as P
    importlib.reload(P)
    monkeypatch.setattr(P, "is_live", lambda svc: False)
    monkeypatch.setattr(P, "MOCK_MODE", False)

    rid = "tp_purged"
    _seed(live_db, rid)

    async def _delete_then_return(*a, **kw):
        s = live_db.SessionLocal()
        try:
            s.query(live_db.RcaDraft).filter(
                live_db.RcaDraft.review_id == rid).delete()
            s.query(live_db.Review).filter(live_db.Review.id == rid).delete()
            s.commit()
        finally:
            s.close()
        return {}
    monkeypatch.setattr(P.claude, "generate_rca_v3", _delete_then_return)

    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass
    assert _row(live_db, rid) is None, \
        "a review deleted mid-run was recreated by the run that was using it"
