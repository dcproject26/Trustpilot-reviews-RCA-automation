"""Setting the booking id by hand — driven, because it shipped a 500.

THIS ENDPOINT WENT OUT WITH A NameError IN IT. `re.sub` on the first line and
`re` was never imported in server/api.py — the only use of the module in the
whole file. Every click returned 500 and the card said "the booking id was not
set", which was true and useless.

Nothing caught it because nothing CALLED it. The UI had four tests, the
markup rendered, the button was proven bound — and the handler on the other
end had never been executed once. A control tested up to the network boundary
and no further is a control whose server half is untested; that is what this
file is for.

Every test here runs the real endpoint function against a real session.
"""
import asyncio
from datetime import datetime

import pytest
from fastapi import BackgroundTasks


@pytest.fixture()
def api_db(live_db):
    """The shared throwaway database, seeded, and with NO module reload.

    Reloading `server.db` here was the obvious way to make the endpoint see
    the temp engine, and it poisons every later test in the process: the
    reloaded module keeps bindings to an engine whose file is deleted at
    teardown. `live_db` already does the reload once, in the right place, and
    `_call` hands the session it builds straight to the endpoint — which is
    the only binding that decides where the queries go.
    """
    s = live_db.SessionLocal()
    s.add(live_db.Review(id="rv1", slack_ts="1", slack_channel="C1", rating=1,
                         author="Ioan", body_original="reserva 33204378",
                         status="draft", received_at=datetime.utcnow()))
    s.add(live_db.RcaDraft(id="draft_rv1", review_id="rv1",
                           booking={"id": "99999999"}, confidence_trail=[]))
    s.commit()
    s.close()
    return live_db


def _call(db, bid, review_id="rv1"):
    import server.api as api
    s = db.SessionLocal()
    try:
        return asyncio.run(api.set_booking_id(
            review_id, api.BookingIdSet(bid=bid), BackgroundTasks(), s))
    finally:
        s.close()


def _draft(db):
    s = db.SessionLocal()
    try:
        return s.query(db.RcaDraft).filter_by(review_id="rv1").first()
    finally:
        s.close()


@pytest.fixture()
def bq(monkeypatch):
    """Control what the warehouse says, and make it LIVE so the verify branch
    actually runs — the default in tests is not live, which skips it."""
    import server.api as api
    import server.services.bigquery_patch as bqp
    monkeypatch.setattr(api, "is_live", lambda k: k == "bigquery")

    def _set(fn):
        monkeypatch.setattr(bqp, "verify_bid", fn)
    return _set


# ── it runs at all ─────────────────────────────────────────────────────────

def test_the_endpoint_does_not_raise_on_a_plain_booking_id(api_db):
    """THE REGRESSION. A NameError here is a 500 and the button is dead."""
    out = _call(api_db, "32142070")
    assert out["ok"] is True, out
    assert out["bid"] == "32142070", out


def test_the_booking_is_actually_stored(api_db):
    _call(api_db, "32142070")
    assert (_draft(api_db).booking or {}).get("id") == "32142070"


def test_an_overwrite_reports_what_it_replaced(api_db):
    """The previous answer must not vanish with nothing recording it."""
    out = _call(api_db, "32142070")
    assert out["replaced"] == "99999999", out
    trail = " ".join(t["text"] for t in (_draft(api_db).confidence_trail or []))
    assert "99999999" in trail, trail


def test_the_provenance_says_a_person_set_it(api_db):
    """A match a person typed and a match the pipeline found must not read the
    same."""
    _call(api_db, "32142070")
    d = _draft(api_db)
    assert d.bid_source == "manual"
    assert d.candidate_state is False
    trail = " ".join(t["text"] for t in (d.confidence_trail or []))
    assert "set by the associate" in trail, trail


# ── what it accepts ────────────────────────────────────────────────────────

@pytest.mark.parametrize("typed", [
    "32142070", " 32142070 ", "#32142070", "32 142 070", "BID 32142070",
])
def test_ids_are_accepted_however_they_were_pasted(api_db, typed):
    """They get copied out of Zendesk and BMS with punctuation around them."""
    assert _call(api_db, typed)["bid"] == "32142070"


@pytest.mark.parametrize("typed", ["", "   ", "abc", "not a booking"])
def test_something_that_is_not_a_number_is_refused_with_a_reason(api_db, typed):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _call(api_db, typed)
    assert e.value.status_code == 400
    assert "booking id" in str(e.value.detail)


def test_a_refusal_leaves_the_existing_booking_alone(api_db):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        _call(api_db, "abc")
    assert (_draft(api_db).booking or {}).get("id") == "99999999"


# ── the warehouse ──────────────────────────────────────────────────────────

def test_an_id_the_warehouse_knows_is_verified_and_carries_its_row(api_db, bq):
    bq(lambda b: {"id": b, "experienceName": "Pena Palace & Park"})
    out = _call(api_db, "32142070")
    assert out["verified"] is True, out
    d = _draft(api_db)
    assert d.match_tier == 1
    assert d.booking.get("experienceName") == "Pena Palace & Park"


def test_an_id_the_warehouse_does_not_know_is_REFUSED(api_db, bq):
    """The safety property. Stored, it would render as a booking with every
    field blank — which reads as a lookup that failed rather than an id that
    was wrong, and a typo would silently become the case's booking."""
    from fastapi import HTTPException
    bq(lambda b: None)
    with pytest.raises(HTTPException) as e:
        _call(api_db, "11111111")
    assert e.value.status_code == 404
    assert e.value.detail["kind"] == "not_found"


def test_a_rejected_id_does_not_touch_the_booking(api_db, bq):
    from fastapi import HTTPException
    bq(lambda b: None)
    with pytest.raises(HTTPException):
        _call(api_db, "11111111")
    assert (_draft(api_db).booking or {}).get("id") == "99999999"


def test_an_unreachable_warehouse_still_lets_the_id_through_and_says_so(api_db, bq):
    """A DIFFERENT FACT from the id being wrong, and it must stay different:
    refusing here would block the one recovery route on the day the warehouse
    is down — exactly when the pipeline is failing to match and someone is
    reaching for this box."""
    def _boom(b):
        raise RuntimeError("BigQuery 503")
    bq(_boom)
    out = _call(api_db, "22222222")
    assert out["ok"] is True, out
    assert out["verified"] is False, out
    assert "failed" in (out["note"] or ""), out
    assert _draft(api_db).match_tier == 2, "an unverified id claimed Tier 1"


def test_an_offline_warehouse_is_named_as_such_rather_than_as_a_failure(api_db,
                                                                        monkeypatch):
    """is_live false is the ordinary local/mock case. Reporting it as a lookup
    failure would send a reader to chase a fault that does not exist.

    is_live is patched explicitly rather than left to the ambient config:
    `live_db` reloads server.config, so whether BigQuery reads as live here
    depends on the environment, and a test that changes answer with the
    machine it runs on is not a test."""
    import server.api as api
    monkeypatch.setattr(api, "is_live", lambda k: False)
    out = _call(api_db, "32142070")
    assert out["verified"] is False
    assert "not connected" in (out["note"] or ""), out


def test_the_two_unverified_reasons_are_distinguishable(api_db, bq, monkeypatch):
    """"We could not ask" and "we asked and it broke" are different, and the
    reader acts on them differently."""
    import server.api as api

    # Offline: the warehouse is not connected, so nothing is asked.
    monkeypatch.setattr(api, "is_live", lambda k: False)
    offline = _call(api_db, "32142070")["note"]

    # Live but broken: it IS asked, and the call fails. `bq` sets is_live
    # itself, so it is applied AFTER the line above — the reverse order leaves
    # both halves on the offline branch and the two notes come back identical,
    # which is the assertion passing for the wrong reason.
    def _boom(b):
        raise RuntimeError("BigQuery 503")
    monkeypatch.setattr(api, "is_live", lambda k: k == "bigquery")
    bq(_boom)
    broken = _call(api_db, "32142071")["note"]
    assert offline and broken, (offline, broken)
    assert offline != broken, (offline, broken)


# ── a review with no draft yet ─────────────────────────────────────────────

def test_a_review_with_no_draft_gets_one(api_db):
    """Untraceable reviews frequently have no draft row, and they are the
    commonest reason to reach for this box at all."""
    s = api_db.SessionLocal()
    s.add(api_db.Review(id="rv2", slack_ts="2", slack_channel="C1", rating=1,
                        author="X", body_original="y", status="new",
                        received_at=datetime.utcnow()))
    s.commit()
    s.close()
    out = _call(api_db, "32142070", review_id="rv2")
    assert out["ok"] is True, out


def test_an_unknown_review_is_a_404_that_names_the_id(api_db):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        _call(api_db, "32142070", review_id="nope")
    assert e.value.status_code == 404
    assert "nope" in str(e.value.detail)
