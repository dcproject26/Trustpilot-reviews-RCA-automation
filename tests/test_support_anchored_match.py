"""Find a booking through the fact that its guest contacted support.

The Zendesk issue pass searches what the guest WROTE. This searches who they
ARE on the booking - name, experience date - restricted to bookings that
actually generated a support contact. fct_support_queries carries the contact
as a category (query_tag / query_type / contact_type), never the guest's
words, so the two are complements: Zendesk finds the phrasing, this finds the
booking behind it and can filter on dates Zendesk knows nothing about.

It is a fallback and nothing else. Every assertion about ordering below exists
because the direct matcher must be able to answer first, every time.

The BigQuery client is monkeypatched, so these run anywhere.
"""
import asyncio
import types

import pytest

from server.services import bigquery as bq


SVEN = {
    "guest_name": "Sven",
    "experience_or_venue": None,
    "city_or_country": None,
    "visit_date_hint": "2026-10-20",
    "issue_terms": ["falsches Datum", "wrong date"],
    "dates_mentioned": ["2026-10-20", "2026-06-20"],
}


def _row(bid, name, exp="Wicked the Musical", visit="2026-10-20",
         contacts=2, tags="REFUND | BOOKING_AMENDMENT"):
    return types.SimpleNamespace(
        booking_id=bid, experience_name=exp, tgid="123", tid="456", vid="789",
        vendor_name="", guest_name=name, booked_on="2026-06-20",
        visit_date=visit, fulfilment_type="",
        contact_count=contacts, contact_tags=tags,
        first_contact="2026-06-21", last_contact="2026-07-02",
    )


class _BQ:
    """Records the SQL and params it was asked to run; returns fixed rows."""

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
            ScalarQueryParameter=_Param,
            ArrayQueryParameter=_Param,
        ))
        return runner
    return _install


def test_name_and_dates_both_reach_the_query(bq_with):
    """Both facts must be used. A name-only filter on a name as common as
    Sven returns a page of strangers; the dates are what make it a match."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    out = asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))

    assert out and out[0]["id"] == "88001"
    sql, params = runner.calls[0]
    assert params["name"] == "Sven Bauer"
    assert params["dates"] == ["2026-10-20", "2026-06-20"]
    assert "experience_date" in sql and "created_at" in sql, \
        "a date the review named may be the visit date or the booking date"


def test_it_refuses_to_run_with_nothing_to_anchor_on(bq_with):
    """No name and no dates means the filter is 'has a support contact',
    which is most of the table. It must return nothing rather than ask."""
    runner = bq_with([_row("88001", "Anyone")])
    assert asyncio.run(bq.find_via_support(
        {"guest_name": None, "dates_mentioned": []}, author="")) == []
    assert runner.calls == [], "no query may be issued with nothing to narrow by"


def test_only_bookings_with_a_contact_are_considered(bq_with):
    """The join to fct_support_queries is the whole point - without it this
    is just a name search over every booking ever made."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "fct_support_queries" in sql
    assert "JOIN" in sql.upper()
    assert "sq.booking_id IS NOT NULL" in sql


def test_the_contact_itself_is_returned_for_the_associate(bq_with):
    """An associate picking between candidates needs to see WHY each one is
    here: how many times that guest wrote in, and what about."""
    bq_with([_row("88001", "Sven Bauer", contacts=3, tags="REFUND | DATE_CHANGE")])
    out = asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    assert out[0]["contact_count"] == 3
    assert "REFUND" in out[0]["contact_tags"]
    assert any("contacted support" in m for m in out[0]["matched_on"]), \
        "the candidate must say what it was matched on"


def test_a_venue_narrows_further_when_the_review_named_one(bq_with):
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(
        dict(SVEN, experience_or_venue="Wicked"), author="Sven Bauer"))
    _, params = runner.calls[0]
    assert params["venue"] == "Wicked"


def test_a_bigquery_failure_is_not_fatal(bq_with, monkeypatch):
    """This runs last, on reviews already headed for Untraceable. A dead
    query must leave them there, not take the whole review down."""
    bq_with()

    def _boom(sql, params):
        raise RuntimeError("403 Access Denied: Table fct_support_queries")

    monkeypatch.setattr(bq, "_run_query", _boom)
    assert asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer")) == []


def test_mock_mode_makes_no_call(bq_with, monkeypatch):
    runner = bq_with([_row("88001", "Sven Bauer")])
    monkeypatch.setattr(bq, "is_live", lambda name: False)
    assert asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer")) == []
    assert runner.calls == []


# ── the fallback must stay a fallback ───────────────────────────────────────

def test_it_runs_after_every_other_path_and_before_untraceable():
    """Read as a guard on the cascade, not on this function: the support
    search must sit below the indicator shortlist, the Zendesk requester
    lookup and both BQ venue+date attempts, and above the Untraceable
    filing. Moving it up would let it answer for a review the existing
    matcher could have matched outright."""
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
    """The one line that keeps it from running on a review that already has
    a match. Deleting the guard must fail a test, not pass silently."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    head = src[:src.find("_sup = await bq.find_via_support(")]
    tail = head[head.rfind("# ── 3c:"):]
    assert "if not cascade_done:" in tail, \
        "the support search is no longer gated on nothing else having matched"
