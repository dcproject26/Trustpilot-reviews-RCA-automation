"""Both fixes, driven through process_review rather than asserted against the
source.

THE GAP THIS CLOSES. The DSS booking enrichment and the warehouse L1/L2
recovery each shipped with unit tests of their DECISION function
(_needs_booking_extra, recover_l1_l2_from_warehouse) and nothing that ran the
pipeline. A decision function can be perfect and still be handed the wrong
input, called after the step that needed it, or have its result dropped on the
floor — and every one of those looks identical to a working fix from a unit
test and a grep of the call site. That is the "validate() was written, tested,
and called by nothing" failure in CLAUDE.md, and a `grep` for the call is the
spelling check it forbids as a guarantee.

So these assert on what the next step actually RECEIVES: the booking dict DSS
is called with, and the l1/l2 written to the draft.
"""
import asyncio
import importlib
from datetime import datetime

import pytest


BID = "33587369"


def _seed(db, rid="tp_enrich"):
    """A review carrying a booking id but no id in its prose — bid_source
    'attachment', which is the direct-BID path that verify_bid serves and the
    one that carried neither isPartnered nor amountUSD."""
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="9.1", slack_channel="C_ORM",
                        rating=1, author="Jimmy",
                        body_original="the venue was shut when we arrived",
                        body_english="the venue was shut when we arrived",
                        reference_number=BID, status="new",
                        received_at=datetime(2026, 8, 22)))
        s.commit()
    finally:
        s.close()
    return rid


# The shape verify_bid really returns: no isPartnered, no amountUSD, and the
# booking date under `date_of_booking` rather than `bookedOn`.
VERIFY_BID_ROW = {
    "id": BID,
    "date_of_booking": "2026-08-21 10:34:00+00:00",
    "date_of_visit": "2026-08-22",
    "experienceName": "Alcatraz Day Tour",
    "tgid": "1", "tid": "2", "vid": "3", "vendorName": "SP",
}


def _patch_common(monkeypatch, P):
    """Live BigQuery, a direct-BID match, and no network anywhere.

    MOCK_MODE IS PINNED, not inherited. `_ai_down` is
    `not MOCK_MODE and not is_live("anthropic")`, so a suite-mate that reloads
    server.config with MOCK_MODE set flips it underneath these tests and the
    trail assertions below start reading a different branch — passing alone and
    failing in the full run, which is worse than either. Pinned, the branch
    under test is the one named in each docstring."""
    monkeypatch.setattr(P, "is_live", lambda svc: svc == "bigquery")
    monkeypatch.setattr(P, "MOCK_MODE", False)
    import server.services.bigquery_patch as bqp
    monkeypatch.setattr(bqp, "verify_bid", lambda bid: dict(VERIFY_BID_ROW))


def test_the_booking_dss_receives_carries_the_enriched_fields(live_db, monkeypatch):
    """The DSS partnered filter and the value note read booking.isPartnered and
    booking.amountUSD. verify_bid carries neither, so on this path DSS ran with
    is_partnered "unknown" and an empty value note — silently, because both
    degrade to a legitimate-looking "unknown" rather than an error.

    This asserts on the dict DSS is actually handed."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra",
                        lambda bid: {"isPartnered": True, "amountUSD": 300.0,
                                     "booking_status": "CONFIRMED"})

    seen = {}

    async def _spy_dss(booking, review_id="", **kw):
        seen["booking"] = dict(booking or {})
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _spy_dss)

    rid = _seed(live_db)
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass          # later steps need a model; DSS is called before them

    assert "booking" in seen, \
        "DSS was never called — the run did not reach it, so this proves nothing"
    assert seen["booking"].get("isPartnered") is True, (
        f"DSS was handed a booking with no isPartnered — the partnered filter "
        f"cannot fire. Got: {seen['booking']}")
    assert seen["booking"].get("amountUSD") == 300.0, (
        f"DSS was handed a booking with no amountUSD — the value note is "
        f"always empty. Got: {seen['booking']}")


def test_an_already_enriched_booking_is_not_queried_twice(live_db, monkeypatch):
    """The guard is idempotent: a path that already merged the extra must not
    pay for the query again. Counted, because "it ran once" and "it ran twice"
    are the same output and different cost."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    calls = []
    import server.services.bigquery as BQ

    def _extra(bid):
        calls.append(bid)
        return {"isPartnered": None, "amountUSD": None}
    monkeypatch.setattr(BQ, "_get_booking_extra", _extra)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    rid = _seed(live_db, "tp_enrich_once")
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass
    assert len(calls) <= 1, \
        f"_get_booking_extra ran {len(calls)} times for one review: {calls}"


def test_an_empty_classification_is_recovered_and_reaches_the_draft(live_db, monkeypatch):
    """The manual-review case: the model returns no L1/L2, and the booking's
    own warehouse tag fills the void. Asserted on the DRAFT — a recovery that
    happens in a local variable and is not written is invisible to the card,
    which is the state the bug was reported in."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra", lambda bid: {"isPartnered": None})

    # The model classifies nothing — the exact input the recovery exists for.
    class _Empty:
        l1 = ""
        l2 = ""
        sub_theme = None
        reasoning = ""
        warnings = []
    import server.services.classifier as C
    async def _empty_classify(*a, **kw):
        return _Empty()
    monkeypatch.setattr(C, "classify", _empty_classify)

    async def _wh(bid):
        return {"l1": "Operations Issue", "l2": "Ticket Issues"}
    monkeypatch.setattr(P.bq, "get_l1_l2_by_bid", _wh)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    rid = _seed(live_db, "tp_recover")
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass

    s = live_db.SessionLocal()
    try:
        d = s.query(live_db.RcaDraft).filter(
            live_db.RcaDraft.review_id == rid).first()
        assert d is not None, "no draft row — the run never reached the persist"
        assert d.l1 == "Operations Issue", (
            f"the warehouse tag never reached the draft: l1={d.l1!r}. The "
            f"classification selects stay blank and everything keyed on L1/L2 "
            f"is skipped — the reported bug.")
        assert d.l2 == "Ticket Issues", f"l2={d.l2!r}"
        texts = " ".join(t.get("text", "") for t in (d.confidence_trail or []))
        assert "recovered from the warehouse" in texts.lower(), (
            f"the classification was filled in with no line saying where it "
            f"came from — it reads as the model's own answer. Trail: {texts[:400]}")
    finally:
        s.close()


def test_a_recovery_is_announced_even_with_the_provider_down(live_db, monkeypatch):
    """THE BUG THIS FILE FOUND. `_ai_down` silences the per-field warnings
    because one sentence already says every model-written field is empty. The
    recovery line was checked AFTER that flag, so it was swallowed in exactly
    the case it matters most — the provider being down is what empties the
    classification, which is what makes the recovery fire. The card showed a
    populated L1/L2 with nothing saying it was the warehouse's tag and not the
    model's answer.

    The run above already has the provider down (is_live is bigquery-only), so
    this asserts the pair that must not collapse: the recovery IS announced,
    and it is NOT dressed up as the model's own repaired answer."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra", lambda bid: {"isPartnered": None})

    class _Empty:
        l1 = ""
        l2 = ""
        sub_theme = None
        reasoning = ""
        warnings = []
    import server.services.classifier as C
    async def _empty_classify(*a, **kw):
        return _Empty()
    monkeypatch.setattr(C, "classify", _empty_classify)

    async def _wh(bid):
        return {"l1": "Operations Issue", "l2": "Ticket Issues"}
    monkeypatch.setattr(P.bq, "get_l1_l2_by_bid", _wh)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    rid = _seed(live_db, "tp_recover_ai_down")
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass

    s = live_db.SessionLocal()
    try:
        d = s.query(live_db.RcaDraft).filter(
            live_db.RcaDraft.review_id == rid).first()
        texts = " ".join(t.get("text", "") for t in (d.confidence_trail or []))
        assert "recovered from the warehouse" in texts.lower(), (
            "with the provider down the recovery line was suppressed — the "
            "reader sees a filled-in classification and no sign of where it "
            "came from")
        assert "was repaired" not in texts.lower(), (
            "a warehouse recovery must not read as the model's answer being "
            "corrected — different fact, different fix")
    finally:
        s.close()


def test_no_classification_line_when_the_provider_is_down_and_nothing_recovered(
        live_db, monkeypatch):
    """The other side of that branch, still holding: with the provider down and
    NO warehouse tag to recover from, the per-field classification warning stays
    suppressed — the single AI-down sentence already covers it, and repeating it
    per field is three warnings for one fact."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra", lambda bid: {"isPartnered": None})

    class _Empty:
        l1 = ""
        l2 = ""
        sub_theme = None
        reasoning = ""
        warnings = []
    import server.services.classifier as C
    async def _empty_classify(*a, **kw):
        return _Empty()
    monkeypatch.setattr(C, "classify", _empty_classify)

    async def _no_tag(bid):
        return {"l1": None, "l2": None}
    monkeypatch.setattr(P.bq, "get_l1_l2_by_bid", _no_tag)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    rid = _seed(live_db, "tp_no_recovery")
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass

    s = live_db.SessionLocal()
    try:
        d = s.query(live_db.RcaDraft).filter(
            live_db.RcaDraft.review_id == rid).first()
        texts = " ".join(t.get("text", "") for t in (d.confidence_trail or []))
        assert "recovered from the warehouse" not in texts.lower(), \
            "a recovery was announced when nothing was recovered"
        assert "classifier returned no" not in texts.lower(), (
            "the per-field classification warning fired with the provider "
            "down — that is the same fact the AI-down sentence already states")
    finally:
        s.close()


def test_the_model_still_wins_when_it_classified(live_db, monkeypatch):
    """The other half: the warehouse must never overwrite a live answer. A
    recovery that also fires on a good classification would silently replace
    the model's judgement with a stale tag."""
    import server.pipeline as P
    importlib.reload(P)
    _patch_common(monkeypatch, P)

    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra", lambda bid: {"isPartnered": None})

    class _Real:
        l1 = "Supply Partner Issue"
        l2 = "Guide No Show"
        sub_theme = None
        reasoning = "the guide never came"
        warnings = []
    import server.services.classifier as C
    async def _real_classify(*a, **kw):
        return _Real()
    monkeypatch.setattr(C, "classify", _real_classify)

    async def _wh(bid):
        return {"l1": "Operations Issue", "l2": "Ticket Issues"}
    monkeypatch.setattr(P.bq, "get_l1_l2_by_bid", _wh)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    rid = _seed(live_db, "tp_no_override")
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass

    s = live_db.SessionLocal()
    try:
        d = s.query(live_db.RcaDraft).filter(
            live_db.RcaDraft.review_id == rid).first()
        assert d is not None
        assert d.l1 == "Supply Partner Issue", (
            f"the warehouse tag overwrote the model's classification: "
            f"l1={d.l1!r}")
        assert d.l2 == "Guide No Show", f"l2={d.l2!r}"
    finally:
        s.close()
