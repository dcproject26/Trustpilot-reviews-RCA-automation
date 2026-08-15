"""A matched booking must survive a failure in any LATER pipeline step.

The draft row used to be created only at the final save step, so a confirmed
Tier 1 match sat in local variables through Zendesk, classification, insights,
DSS, RCA and drafting. One exception in any of those discarded the match and
the review appeared in Untraceable - indistinguishable from a review whose BID
we never found, except that we had found it.
"""
import asyncio
import os
import tempfile

import pytest
from tests.conftest import drop_temp_db


@pytest.fixture()
def db_env(monkeypatch):
    """A throwaway SQLite DB with the real schema."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import importlib
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    yield db
    drop_temp_db(tmp.name)


def _seed(db, rid="tp_test_1", bid="32908218"):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="David Test", body_original=f"awful, booking {bid}",
                        reference_number=bid, status="new"))
        s.commit()
    finally:
        s.close()
    return rid


def _session_factory_failing_final_save(db):
    """A SessionLocal whose commit raises once the FINAL save is in flight.

    This is the failure that actually happens in production: the per-step
    try/except blocks absorb a dead connector and the run continues, so the
    only thing that truly loses data is the last commit itself - which is
    exactly what a missing DB column does ("no such column: template_name").
    Detected by the presence of suggested_response, which only the final save
    assigns.
    """
    real = db.SessionLocal

    class Proxy:
        def __init__(self):
            self._s = real()

        def commit(self):
            for obj in list(self._s.dirty) + list(self._s.new):
                if isinstance(obj, db.RcaDraft) and obj.suggested_response is not None:
                    raise RuntimeError("no such column: rca_drafts.template_name")
            return self._s.commit()

        def __getattr__(self, name):
            return getattr(self._s, name)

    return Proxy


def test_match_survives_a_failing_final_save(db_env, monkeypatch):
    db = db_env
    rid = _seed(db)

    import importlib
    import server.pipeline as P
    importlib.reload(P)

    monkeypatch.setattr(P, "verify_bid", lambda bid: {
        "id": bid, "tid": "43605", "vid": "4040", "tgid": "22238",
        "experienceName": "Test Experience",
        "_match": {"tier": 1, "confidence": "high", "method": "BID in review text"},
    }, raising=False)
    monkeypatch.setattr(P, "SessionLocal",
                        _session_factory_failing_final_save(db), raising=False)

    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass

    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        assert d is not None, (
            "no draft row: the final commit failed and took the match with it, "
            "so a review with a confirmed BID shows as Untraceable")
        assert d.match_tier == 1, f"match_tier lost (got {d.match_tier!r})"
        assert (d.booking or {}).get("id") == "32908218", "booking lost"
        assert d.bid_source, "bid_source lost"
        assert d.confidence_trail, "confidence trail lost"
    finally:
        s.close()


def test_unmatched_review_also_leaves_a_row(db_env, monkeypatch):
    """A genuine miss must leave a row too, with tier None, so 'searched and
    missed' is distinguishable from 'the run never got that far'."""
    db = db_env
    s = db.SessionLocal()
    try:
        s.add(db.Review(id="tp_test_2", slack_ts="2.0", slack_channel="C1",
                        rating=1, author="Nobody", body_original="bad time",
                        reference_number=None, status="new"))
        s.commit()
    finally:
        s.close()

    import importlib
    import server.pipeline as P
    importlib.reload(P)
    monkeypatch.setattr(P, "verify_bid", lambda bid: None, raising=False)
    monkeypatch.setattr(P, "SessionLocal",
                        _session_factory_failing_final_save(db), raising=False)
    try:
        asyncio.run(P.process_review("tp_test_2"))
    except Exception:
        pass
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_test_2").first()
        assert d is not None, "even an unmatched review must leave a draft row"
        assert d.match_tier is None
    finally:
        s.close()
