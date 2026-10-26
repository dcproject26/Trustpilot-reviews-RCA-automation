"""Find a booking through the support contact behind it.

3a and 3b already ask BigQuery which bookings match the venue and a date
window. This asks which of those ALSO produced a complaint to support, which
is a much smaller set and one where every member has a reason to be in front
of an associate. It is also a second attempt at the date: the day and month
the review named, in any adjacent year, rather than a window around the post
date.

It deliberately does not match on the guest name. primary_guest_name is a PII
hash on every booking behind a support contact - measured over the window,
639,109 rows and 639,109 hashes. An earlier version searched that column with
a substring LIKE and returned 'ab24TSVenneb4T3CkHFUFaGM' as a match for a
guest called Sven, because the letters SVen sit inside the hash. It offered a
Barcelona walking tour as the booking behind a review about a musical. The
name is gone; these tests keep it gone.

The BigQuery client is monkeypatched, so these run anywhere.
"""
import asyncio
import types

import pytest

from server.services import bigquery as bq


SVEN = {
    "guest_name": "Sven",
    "experience_or_venue": "musical ticket",
    "city_or_country": None,
    "visit_date_hint": "2026-10-20",
    "issue_terms": ["falsches Datum", "wrong date"],
    "dates_mentioned": ["2026-10-20", "2026-06-20"],
}
TGIDS = ["4001", "4002"]


def _row(bid, name, exp="Wicked the Musical", visit="2026-10-20",
         contacts=2, tags="REFUND | BOOKING_AMENDMENT"):
    return types.SimpleNamespace(
        booking_id=bid, experience_name=exp, tgid="4001", tid="456", vid="789",
        vendor_name="", guest_name=name, booked_on="2026-06-20",
        visit_date=visit, fulfilment_type="",
        contact_count=contacts, contact_tags=tags,
        first_contact="2026-06-21", last_contact="2026-07-02",
    )


class _BQ:
    def __init__(self, rows=()):
        self.rows = list(rows)
        self.calls = []

    def __call__(self, sql, params):
        self.calls.append((sql, {p.name: p.value for p in params}))
        return self.rows


class _Param:
    def __init__(self, name, _type, value):
        self.name, self.value = name, value


@pytest.fixture
def bq_with(monkeypatch):
    def _install(rows=()):
        runner = _BQ(rows)
        monkeypatch.setattr(bq, "is_live", lambda name: True)
        monkeypatch.setattr(bq, "_run_query", runner)
        monkeypatch.setattr(bq, "_bqlib", types.SimpleNamespace(
            ScalarQueryParameter=_Param, ArrayQueryParameter=_Param))
        return runner
    return _install


# ── the guest name is not usable and must stay out ──────────────────────────

def test_the_guest_name_is_never_searched_for():
    """639,109 of 639,109 bookings behind a support contact carry a PII hash
    in primary_guest_name. A filter there cannot match anything - it can only
    exclude everything - and the substring version actively produced wrong
    bookings."""
    sql, params = bq.support_search_sql(SVEN, tgids=TGIDS)
    assert sql is not None
    where = sql[sql.find("WHERE"):]
    assert "primary_guest_name" not in where, \
        "the guest name is a hash; it must not be in the WHERE clause"
    assert "name" not in {p.name for p in params}


def test_a_hashed_name_is_recognised_as_one():
    assert bq.is_hashed_name("ab24TSVenneb4T3CkHFUFaGM") is True
    assert bq.is_hashed_name("Sven Bauer") is False
    assert bq.is_hashed_name("Konstantin") is False, "a long real name is not a hash"


def test_a_hashed_name_is_not_shown_as_a_guest(bq_with):
    """The row still carries the hash. Rendering it in the guest column of a
    candidate card presents a hash to an associate as a person's name."""
    bq_with([_row("88001", "ab24TSVenneb4T3CkHFUFaGM")])
    out = asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    assert out and out[0]["guestName"] == ""


def test_a_real_name_would_still_be_shown(bq_with):
    bq_with([_row("88001", "Sven Bauer")])
    out = asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    assert out[0]["guestName"] == "Sven Bauer"


# ── what it does anchor on ──────────────────────────────────────────────────

def test_it_needs_both_an_experience_and_a_date(bq_with):
    """Either alone is not a match. Every booking for one experience is a long
    list; every booking on one date is a longer one."""
    runner = bq_with([_row("88001", "x")])
    assert asyncio.run(bq.find_via_support(SVEN, tgids=[])) == []
    assert asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=[]), tgids=TGIDS)) == []
    assert runner.calls == [], "neither case may reach the query"


def test_it_matches_resolved_tgids_not_the_words_the_guest_used(bq_with):
    """Guests name product types - "musical ticket", "skip-the-line entry".
    None of those is an experience_name, and the pipeline has already resolved
    what the guest meant into real ids."""
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, params = runner.calls[0]
    assert params["tgids"] == TGIDS
    assert "experience_id" in sql
    assert "experience_name" not in sql[sql.find("WHERE"):], \
        "the free-text venue match is what returned nothing for 'musical ticket'"


def test_the_day_and_month_are_matched_not_the_guessed_year(bq_with):
    """Guests write "20/10", not "20/10/2025". The day and month are facts
    from the review; the year is what extraction inferred from the post date.
    Pinning the filter to the inferred part while discarding the observed part
    threw away every candidate on Sven's review."""
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, _ = runner.calls[0]
    assert "EXTRACT(MONTH FROM DATE(d))" in sql and "EXTRACT(DAY   FROM DATE(d))" in sql
    assert "SAFE.DATE" in sql, "29 February does not exist in every year"
    assert "EXTRACT(YEAR FROM DATE(d))) <= 1" in sql, \
        "loose is not open — a booking five years out is not this one"


def test_both_the_visit_and_the_booking_date_are_checked(bq_with):
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, _ = runner.calls[0]
    assert "experience_date" in sql and "created_at" in sql


def test_a_date_the_model_did_not_format_is_dropped_not_passed(bq_with):
    """Dates go into the SQL as DATE(@d). One unparseable value raises inside
    BigQuery and takes the whole search down."""
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=["2026-10-20", "20.06.2026", "sometime in June",
                                    "2026-13-45", ""]), tgids=TGIDS))
    _, params = runner.calls[0]
    assert params["dates"] == ["2026-10-20"]


def test_a_date_inside_a_longer_answer_is_still_used(bq_with):
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=["2026-06-20 (the date on the voucher)"]),
        tgids=TGIDS))
    _, params = runner.calls[0]
    assert params["dates"] == ["2026-06-20"]


# ── the support contact itself ──────────────────────────────────────────────

def test_only_bookings_with_a_contact_are_considered(bq_with):
    """The join to fct_support_queries is the whole point — without it this is
    3a with a different date rule."""
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, _ = runner.calls[0]
    assert "fct_support_queries" in sql and "JOIN" in sql.upper()
    assert "sq.booking_id IS NOT NULL" in sql


def test_the_join_matches_the_column_types(bq_with):
    """sq.booking_id is a STRING, b.booking_id an INT64. Measured at 98% of
    contacts resolving to a booking, so this direction is correct."""
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, _ = runner.calls[0]
    assert "SAFE_CAST(sq.booking_id AS INT64) = b.booking_id" in sql


def test_the_contact_is_returned_for_the_associate(bq_with):
    """With no guest name to show, the contact IS the evidence: how many times
    someone on this booking wrote in, and what about."""
    bq_with([_row("88001", "x", contacts=3, tags="REFUND | DATE_CHANGE")])
    out = asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    assert out[0]["contact_count"] == 3
    assert "REFUND" in out[0]["contact_tags"]
    assert any("contacted support" in m for m in out[0]["matched_on"])


def test_the_scan_is_bounded_in_time(bq_with):
    runner = bq_with([_row("88001", "x")])
    asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS))
    sql, _ = runner.calls[0]
    assert "query_created_at >=" in sql and "INTERVAL" in sql


def test_a_bigquery_failure_is_not_fatal(bq_with, monkeypatch):
    """This runs last, on reviews already headed for Untraceable."""
    bq_with()

    def _boom(sql, params):
        raise RuntimeError("403 Access Denied")

    monkeypatch.setattr(bq, "_run_query", _boom)
    assert asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS)) == []


def test_mock_mode_makes_no_call(bq_with, monkeypatch):
    runner = bq_with([_row("88001", "x")])
    monkeypatch.setattr(bq, "is_live", lambda name: False)
    assert asyncio.run(bq.find_via_support(SVEN, tgids=TGIDS)) == []
    assert runner.calls == []


# ── the fallback must stay a fallback ───────────────────────────────────────

def test_it_runs_after_every_other_path_and_before_untraceable():
    """A guard on the cascade: the support search must sit below the indicator
    shortlist, the Zendesk requester lookup and both BQ venue+date attempts,
    and above the Untraceable filing."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    order = [
        'narrowing_path = "indicator_shortlist"',
        "zendesk requester",
        'rows = _run_bq_attempt("venue_date_30"',
        'rows = _run_bq_attempt("venue_date_60"',
        "_sup = await bq.find_via_support(",
        "Date-only matching removed",
    ]
    found = [src.find(s) for s in order]
    assert all(i >= 0 for i in found), \
        f"cascade landmark missing: {[s for s, i in zip(order, found) if i < 0]}"
    assert found == sorted(found), \
        "the support-anchored search moved out of its place in the cascade"


def test_it_is_guarded_by_cascade_done():
    src = open("server/pipeline.py", encoding="utf-8").read()
    head = src[:src.find("_sup = await bq.find_via_support(")]
    tail = head[head.rfind("# ── 3c:"):]
    assert "if not cascade_done:" in tail, \
        "the support search is no longer gated on nothing else having matched"


def test_the_pipeline_passes_the_resolved_tgids():
    """Without them the search declines to run, silently, on every review."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.find("_sup = await bq.find_via_support(")
    assert "tgids=tgids" in src[i:i + 200]


def test_the_shortlist_step_is_reachable_for_a_review_with_no_name():
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.find("Step 2: indicator shortlist")
    gate = src[i:src.find("await zendesk.shortlist(", i)]
    assert "_issue_terms" in gate


def test_the_guest_name_survives_onto_the_candidate():
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.find('for _r in _sup[:8]:')
    block = src[i:i + 600]
    assert "primary_guest_name=" in block and "guestName" in block
