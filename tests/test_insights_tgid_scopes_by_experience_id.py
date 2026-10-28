"""The experience-scoped tiles key on the real experience_id, not the tour_id.

The TGID fix (25d5c1f, cc306ef) made the card show the real TGID =
experience_id, carried on the booking as `experienceId`. But three
"Experience Insights" tiles that are meant to be PER-EXPERIENCE still scoped by
`b.tour_id = @tgid`, because the realtime migration conflated tgid with
tour_id. experience_id is NOT on the bookings fact — it lives on
fct_orders_lambda, joined on order_id. So those three tiles must:

  * join bookings -> orders on b.order_id = o.order_id, and
  * filter CAST(o.experience_id AS STRING) = @tgid, with
  * @tgid bound to booking["experienceId"], not booking["tgid"].

The three are: the experience average rating (sql_e_tgid), the experience
fulfilment rate (sql_h2) and the experience "why it failed" breakdown
(sql_why_tgid). The recurrence (TID+VID) and the vendor-wide (VID) queries are
LEFT UNCHANGED — they genuinely want the tour/vendor grain.

Driven through get_insights against a stubbed warehouse. The queries cannot be
run here, so what is checked is the SQL text issued and the params bound — the
behaviour the panel depends on — per rule 2 (a test that only asserts a string
exists in source is a spelling check).

The fixture gives tgid and experienceId DIFFERENT values, so "scoped by the
experience" and "scoped by the tour" are told apart by which value reaches
@tgid, not merely by which column name appears.
"""
import asyncio

import pytest

import server.services.insights as ins


TOUR_ID = "22238"       # booking["tgid"] — the tour_id, NOT the experience
EXP_ID  = "3037"        # booking["experienceId"] — the real TGID

BOOKING = {"id": "32908218", "tid": "43605", "vid": "4040",
           "tgid": TOUR_ID, "experienceId": EXP_ID,
           "date_of_visit": "2026-06-01"}


def _drive(booking=None, l1="Operations Issue", l2="Ticket Issues",
           window="30d"):
    """get_insights with every query stubbed. Returns (payload, calls).

    calls is a list of (sql, params) for every query the run issued.
    """
    calls = []

    async def fake_run(sql, params):
        calls.append((sql, params))
        return [{"c": 7, "ids": ["111", "222"], "avg_rating": 4.2,
                 "n_ratings": 9, "completed": 8,
                 "completed_by_booking_status": 8, "unfulfilled": 1,
                 "total": 10, "status": "Cancelled",
                 "ctype": "Cancelled By Vendor"}]

    real_run, real_live = ins._run, ins.is_live
    ins._run = fake_run
    ins.is_live = lambda *a, **k: True
    try:
        out = asyncio.run(ins.get_insights(
            booking=BOOKING if booking is None else booking,
            l1=l1, l2=l2, window=window))
    finally:
        ins._run, ins.is_live = real_run, real_live
    return out, calls


# ── the three experience-scoped queries ────────────────────────────────────

def _experience_scoped(calls):
    """Every query that filters on experience_id — the three TGID tiles."""
    return [(s, p) for s, p in calls
            if "CAST(o.experience_id AS STRING) = @tgid" in s]


def test_three_queries_scope_by_experience_id():
    """The rating, fulfilment and why tiles each scope by experience_id."""
    _, calls = _drive()
    scoped = _experience_scoped(calls)
    kinds = {"rating": any("avg_rating" in s for s, _ in scoped),
             "fulfilment": any("avg_rating" not in s and "GROUP BY" not in s
                               and "fulfilment_status" in s for s, _ in scoped),
             "why": any("GROUP BY status, ctype" in s for s, _ in scoped)}
    assert all(kinds.values()), (
        f"not all three experience tiles scope by experience_id: {kinds}")


def test_each_experience_query_joins_orders_on_order_id():
    """experience_id lives on the order, so each must reach it through the join
    — the column cannot be read off the booking."""
    _, calls = _drive()
    scoped = _experience_scoped(calls)
    assert scoped, "no experience-scoped query ran at all"
    for sql, _ in scoped:
        assert "ON b.order_id = o.order_id" in sql, (
            "an experience-scoped query filters o.experience_id without "
            f"joining the order:\n{sql[:300]}")


def test_experience_queries_bind_tgid_to_the_experience_id_not_the_tour():
    """The whole bug: @tgid used to carry the tour_id. It must now carry the
    experienceId. Told apart because the fixture gives them different values."""
    _, calls = _drive()
    scoped = _experience_scoped(calls)
    assert scoped, "no experience-scoped query ran at all"
    for sql, params in scoped:
        assert params.get("tgid") == EXP_ID, (
            f"@tgid bound to {params.get('tgid')!r}, expected the experience "
            f"id {EXP_ID!r}; binding the tour id {TOUR_ID!r} is the defect")
        assert params.get("tgid") != TOUR_ID


def test_no_experience_tile_still_scopes_by_tour_id():
    """The inverse: the mutation this replaces was `b.tour_id = @tgid`. No
    query that reads avg_rating or the fulfilment fields may carry it."""
    _, calls = _drive()
    offenders = [s for s, _ in calls
                 if "b.tour_id = @tgid" in s]
    assert not offenders, (
        f"{len(offenders)} quer(ies) still scope the experience by tour_id:\n"
        + "\n---\n".join(s[:200] for s in offenders))


# ── the queries that must NOT move ─────────────────────────────────────────

def test_recurrence_stays_tid_and_vid():
    """TID+VID recurrence is untouched: tour_id = @tid AND vendor_id = @vid,
    and it does not join the orders table."""
    _, calls = _drive()
    # Recurrence is the tid-scoped reviews pair; the same-day reviews tile also
    # counts r.booking_id but scopes by vendor_id + a single anchor date, so it
    # is excluded by requiring @tid.
    rec = [s for s, _ in calls
           if "COUNT(DISTINCT r.booking_id)" in s and "avg_rating" not in s
           and "@tid" in s]
    assert rec, "no recurrence query ran"
    for sql in rec:
        assert "b.tour_id = @tid AND b.vendor_id = @vid" in sql
        assert "o.experience_id" not in sql, (
            "the recurrence query was dragged onto the experience grain")


def test_vid_completion_stays_vendor_wide_without_the_orders_join():
    """The VID fulfilment rate answers 'is this vendor failing across
    everything' — vendor_id only, no experience_id, no orders join."""
    _, calls = _drive()
    vid = [s for s, _ in calls
           if "fulfilment_status" in s and "vendor_id = @vid" in s
           and "tour_id = @tid" not in s and "@tgid" not in s
           and "GROUP BY" not in s]
    assert vid, "the vendor-wide completion query is gone"
    for sql in vid:
        assert "o.experience_id" not in sql
        assert "ON b.order_id = o.order_id" not in sql


def test_tidvid_completion_stays_on_the_pair():
    _, calls = _drive()
    tv = [s for s, _ in calls
          if "fulfilment_status" in s and "tour_id = @tid" in s
          and "vendor_id = @vid" in s]
    assert tv, "no TID+VID completion query ran"
    for sql in tv:
        assert "o.experience_id" not in sql


# ── missing experience_id is a dash, not a tour-scoped number ──────────────

def test_without_an_experience_id_the_tiles_are_skipped_not_backfilled():
    """Rule 1: ran-and-found-nothing vs did-not-run. A booking with a tour_id
    but no resolvable experience_id must show a dash on the experience tiles —
    NOT fall back to a tour-scoped number under an experience label, which is
    exactly the bug. So: no experience-scoped query runs, and the tiles come
    back empty rather than filled.
    """
    booking = {"tid": "43605", "vid": "4040", "tgid": TOUR_ID,
               "date_of_visit": "2026-06-01"}   # no experienceId, no digit id
    out, calls = _drive(booking=booking)
    assert not _experience_scoped(calls), (
        "an experience-scoped query ran with no experience_id to key on")
    assert out["rating_tgid"]["avg"] is None, "rating backfilled without an exp id"
    assert out["ff_tgid"] is None, "fulfilment backfilled without an exp id"


def test_experience_id_is_pulled_from_the_resolve_lookup(monkeypatch):
    """When the draft carries only a booking id, get_insights resolves the rest
    through verify_bid — and must pull experienceId out of that lookup too, or
    the experience tiles stay dark for every resolved-by-bid card. Drives the
    resolve path with a stubbed verify_bid and asserts the experience-scoped
    queries run bound to the RESOLVED experience id."""
    import server.services.bigquery_patch as patch

    resolved = {"tid": "43605", "vid": "4040", "tgid": TOUR_ID,
                "experienceId": "5555", "date_of_visit": "2026-06-01"}
    monkeypatch.setattr(patch, "verify_bid", lambda bid: dict(resolved))

    # Only a booking id on the draft — everything else comes from the lookup.
    out, calls = _drive(booking={"id": "32908218"})
    scoped = _experience_scoped(calls)
    assert scoped, "the experience tiles never ran after resolving by bid"
    for sql, params in scoped:
        assert params.get("tgid") == "5555", (
            "experienceId was not carried out of the resolve lookup — the "
            "experience tiles would be dark for every resolved-by-bid card")


def test_the_fallback_does_not_quietly_use_the_tour_id():
    """The specific failure mode: experienceId absent, tgid present. The tile
    must not silently reuse tgid as the experience scope."""
    booking = {"tid": "43605", "vid": "4040", "tgid": TOUR_ID,
               "date_of_visit": "2026-06-01"}
    _, calls = _drive(booking=booking)
    bound_tours = [p.get("tgid") for s, p in calls if "@tgid" in s]
    assert TOUR_ID not in bound_tours, (
        "an @tgid query bound the tour_id as a stand-in for the experience")
