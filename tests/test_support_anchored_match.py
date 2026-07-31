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
    assert params["name"] == "Bauer", \
        "the surname is what gets matched — see the display-name tests below"
    assert params["dates"] == ["2026-10-20", "2026-06-20"]
    assert "experience_date" in sql and "created_at" in sql, \
        "a date the review named may be the visit date or the booking date"


def test_a_title_is_not_searched_for_as_a_name(bq_with):
    """"Frau Nicole" is a title and a first name. LIKE '%Frau Nicole%' against
    a booking name matches nothing, ever - the same trap that produced "No
    booking found for Frau"."""
    runner = bq_with([_row("88001", "Nicole Weber")])
    asyncio.run(bq.find_via_support(SVEN, author="Frau Nicole"))
    _, params = runner.calls[0]
    assert params["name"] == "Nicole"


def test_an_abbreviated_surname_falls_back_to_the_first_name(bq_with):
    """Trustpilot shows "Sven B.". A surname of one letter is not searchable,
    so the first name is used rather than matching on 'B'."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven B."))
    _, params = runner.calls[0]
    assert params["name"] == "Sven"


def test_a_date_the_model_did_not_format_is_dropped_not_passed(bq_with):
    """Dates go into the SQL as DATE(@d). One unparseable value raises inside
    BigQuery and takes the whole search down, so junk is dropped here."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=["2026-10-20", "20.06.2026", "sometime in June",
                                    "2026-13-45", ""]),
        author="Sven Bauer"))
    _, params = runner.calls[0]
    assert params["dates"] == ["2026-10-20"], \
        "only clean ISO dates may reach the query"


def test_a_date_inside_a_longer_answer_is_still_used(bq_with):
    """The model sometimes annotates: '2026-06-20 (the date on the voucher)'.
    That is a usable date, not junk."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=["2026-06-20 (the date on the voucher)"]),
        author="Sven Bauer"))
    _, params = runner.calls[0]
    assert params["dates"] == ["2026-06-20"]


def test_the_scan_is_bounded_in_time(bq_with):
    """Unbounded, this joins every support contact ever recorded to every
    booking on every unmatched review."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "query_created_at >=" in sql and "INTERVAL" in sql


def test_the_join_matches_the_column_types(bq_with):
    """sq.booking_id is a STRING, b.booking_id an INT64. Casting the bookings
    side to STRING instead defeats pruning on the larger table, and a
    non-numeric id would raise rather than simply not match."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "SAFE_CAST(sq.booking_id AS INT64) = b.booking_id" in sql


def test_it_refuses_to_run_with_nothing_to_anchor_on(bq_with):
    """No name and no dates means the filter is 'has a support contact',
    which is most of the table. It must return nothing rather than ask."""
    runner = bq_with([_row("88001", "Anyone")])
    assert asyncio.run(bq.find_via_support(
        {"guest_name": None, "dates_mentioned": []}, author="")) == []
    assert runner.calls == [], "no query may be issued with nothing to narrow by"


def test_a_hashed_guest_name_is_not_a_guest_name(bq_with):
    """fct_bookings.primary_guest_name is a PII hash on many rows.
    'ab24TSVenneb4T3CkHFUFaGM' contains the letters SVen, and a substring
    search for a guest called Sven returned it - handing back a Barcelona
    walking tour as the booking behind a review about a musical."""
    assert bq.is_hashed_name("ab24TSVenneb4T3CkHFUFaGM") is True
    assert bq.is_hashed_name("Sven Bauer") is False
    assert bq.is_hashed_name("Konstantin") is False, "a long real name is not a hash"

    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "LENGTH(b.primary_guest_name) >= 16" in sql, \
        "hashed rows must be excluded before the name is compared"


def test_the_name_must_match_a_whole_word(bq_with):
    """Substring matching is what let a hash match. Even among real names,
    'Sven' inside 'Svensson' is a different guest."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "REGEXP_CONTAINS(LOWER(b.primary_guest_name)" in sql
    assert "(^|[^a-z])" in sql and "([^a-z]|$)" in sql
    assert "LIKE LOWER(CONCAT('%', @name, '%'))" not in sql, \
        "the substring match is what produced the false positive"


def test_a_hashed_row_that_slips_through_is_dropped(bq_with):
    """The SQL excludes them, but this is the check that decides whether a
    booking is shown to an associate as matching their guest."""
    bq_with([_row("88001", "ab24TSVenneb4T3CkHFUFaGM")])
    out = asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    assert out == [], "a hash cannot corroborate a name"


def test_a_display_name_cannot_break_the_query(bq_with):
    """The name goes into a regex in the SQL. "Ann (Annie)" would otherwise
    raise rather than simply not match."""
    runner = bq_with([_row("88001", "Annie Hall")])
    asyncio.run(bq.find_via_support(SVEN, author="Ann (Annie)"))
    _, params = runner.calls[0]
    assert "(" not in params["name"] and ")" not in params["name"]


def test_the_day_and_month_are_matched_not_the_guessed_year(bq_with):
    """Guests write "20/10", not "20/10/2025". The day and month are facts
    from the review; the year is what extraction inferred from the post date,
    and the prompt says so. Pinning the filter to the inferred part while
    discarding the observed part is what made Sven's search return nothing -
    sixteen bookings under his name with a support contact, none surviving."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "EXTRACT(MONTH FROM DATE(d))" in sql and "EXTRACT(DAY   FROM DATE(d))" in sql, \
        "the day and month the guest wrote must be what is compared"
    assert "SAFE.DATE" in sql, "29 February does not exist in every year"
    assert "<= 1)" in sql, "the year must still be bounded, not ignored"


def test_a_wildly_different_year_is_still_rejected(bq_with):
    """Loose is not the same as open. A booking on 20 October five years ago
    is not this review's booking."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    asyncio.run(bq.find_via_support(SVEN, author="Sven Bauer"))
    sql, _ = runner.calls[0]
    assert "EXTRACT(YEAR FROM DATE(d))) <= 1" in sql


def test_a_name_on_its_own_is_not_enough_to_search_on(bq_with):
    """"Guests called Sven who contacted support in the last 18 months" is a
    long list. Returning the most recent eight as candidates dresses noise up
    as a match, which is worse than saying nothing - the associate has no way
    to tell the difference."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    out = asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=[], experience_or_venue=None),
        author="Sven Bauer"))
    assert out == []
    assert runner.calls == [], "a bare name must not reach the query"


def test_a_name_with_a_venue_is_enough(bq_with):
    """Two agreements, even with no date."""
    runner = bq_with([_row("88001", "Sven Bauer")])
    out = asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=[], experience_or_venue="Wicked"),
        author="Sven Bauer"))
    assert out and runner.calls


def test_a_venue_that_is_really_a_product_type_is_dropped_and_retried(bq_with,
                                                                     monkeypatch):
    """Guests name product types: "musical ticket", "skip-the-line entry".
    None of those is an experience_name, so ANDing it in turns a search that
    would have found something into one that finds nothing."""
    calls = []

    def _runner(sql, params):
        calls.append({p.name: p.value for p in params})
        # The venue filter matches nothing; without it there is a booking.
        return [] if "venue" in calls[-1] else [_row("88001", "Sven Bauer")]

    monkeypatch.setattr(bq, "is_live", lambda name: True)
    monkeypatch.setattr(bq, "_run_query", _runner)
    monkeypatch.setattr(bq, "_bqlib", types.SimpleNamespace(
        ScalarQueryParameter=_Param, ArrayQueryParameter=_Param))

    out = asyncio.run(bq.find_via_support(
        dict(SVEN, experience_or_venue="musical ticket"), author="Sven Bauer"))

    assert len(calls) == 2, "the venue should have been dropped and retried"
    assert "venue" in calls[0] and "venue" not in calls[1]
    assert out and out[0]["id"] == "88001"
    assert not any("venue" in m for m in out[0]["matched_on"]), \
        "the result must not claim a venue agreement that was dropped"


def test_the_venue_is_not_dropped_when_it_is_holding_the_search_up(bq_with):
    """With no dates, name+venue IS the anchor. Dropping the venue would leave
    a bare name, which is exactly what the rule above forbids."""
    runner = bq_with([])
    out = asyncio.run(bq.find_via_support(
        dict(SVEN, dates_mentioned=[], experience_or_venue="Wicked"),
        author="Sven Bauer"))
    assert out == []
    assert len(runner.calls) == 1, "no retry may strip the anchor itself"


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


def test_the_shortlist_step_is_reachable_for_a_review_with_no_name():
    """shortlist()'s second pass can match on the problem alone, but only if
    the pipeline calls it. Gating that step on name-or-venue meant an
    anonymous review skipped it entirely, so the pass could never run on the
    reviews it was written for."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.find("Step 2: indicator shortlist")
    gate = src[i:src.find("await zendesk.shortlist(", i)]
    assert "_issue_terms" in gate, \
        "the indicator-shortlist step is unreachable for a review with only a problem"


def test_the_guest_name_survives_onto_the_candidate():
    """The associate picks between candidates on the guest name. _row_to_dict
    calls it guestName and _make_candidate reads primary_guest_name, so the
    bridge between them is load-bearing, not tidying."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    i = src.find('for _r in _sup[:8]:')
    block = src[i:i + 600]
    assert "primary_guest_name=" in block and "guestName" in block


def test_it_is_guarded_by_cascade_done():
    """The one line that keeps it from running on a review that already has
    a match. Deleting the guard must fail a test, not pass silently."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    head = src[:src.find("_sup = await bq.find_via_support(")]
    tail = head[head.rfind("# ── 3c:"):]
    assert "if not cascade_done:" in tail, \
        "the support search is no longer gated on nothing else having matched"
