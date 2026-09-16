"""The Reporting page's query engine: the projection, the grouping arithmetic,
and the honesty guarantees (unset vs zero, totals not summed, unnesting said out
loud). Driven against the functions, not asserted against source text."""
from datetime import datetime, timedelta

import pytest

from server.services.reporting_query import (
    DIMENSIONS, DIM_BY_KEY, MEASURES, UNSET, UNASSIGNED,
    project, run_query, records, values_of, field_registry,
    _tier_label, _norm_owner)


def _rec(**kw):
    """An analytics record with sane defaults, so each test states only what it
    is about."""
    base = dict(review_id="r", date="2026-09-01", rating=1, language="English",
                status="sent", experience=None, vendor=None, tgid=None,
                fulfilment_type=None, booking_status=None, tier="Tier 1",
                traceable="Traceable", match_method="none", l1="Operations Issue",
                l2="Ticket Issues", sub_themes=[], scenarios=[],
                overlay_scenarios=[], claim_accuracy=[], resolution_given="No",
                takedown=None, outcome_category=None, dss_followed=None,
                owner="Avi", sent_route=None, close_reason=None,
                prompt_version=None, solved=True, posted=True, issue_count=2,
                zd_count=3, flag_count=1, tts_hours=10.0)
    base.update(kw)
    return base


# ── the tier label: the same three buckets the daily digest uses ────────────

def test_tier_is_only_three_buckets():
    assert _tier_label(1, "", "") == "Tier 1"
    assert _tier_label(2, "", "") == "Tier 2"
    assert _tier_label(None, "", "") == "Untraceable"


def test_declared_untraceable_overrides_a_stale_tier():
    # A person saying "no booking" beats a tentative match still on the draft.
    assert _tier_label(2, "Untraceable — asked the guest for a reference", "") == "Untraceable"
    assert _tier_label(1, "", "Marked untraceable by associate") == "Untraceable"


# ── test accounts: drop the ATTRIBUTION, never the review ───────────────────

def test_test_accounts_are_not_credited_as_people():
    for name in ("Test", "test", "QA", "demo", ""):
        assert _norm_owner(name) == UNASSIGNED
    assert _norm_owner("Devshree") == "Devshree"


def test_a_test_owned_review_still_counts_everywhere_else():
    # THE POINT: the review is real, only the owner is a test value. Dropping the
    # row would understate inflow and lose a genuine review.
    # Owners arrive already normalised by project(); build them the same way.
    rows = [_rec(owner=_norm_owner("Test"), tier="Tier 1"),
            _rec(owner=_norm_owner("Devshree"), tier="Tier 2")]
    by_owner = run_query(rows, ["owner"], ["count"])
    names = {r["key"][0] for r in by_owner["rows"]}
    assert names == {UNASSIGNED, "Devshree"}          # no "Test" as a person
    assert by_owner["totals"]["count"] == 2           # but both reviews counted
    by_tier = run_query(rows, ["tier"], ["count"])
    assert by_tier["totals"]["count"] == 2


# ── rule 1: "no value recorded" must not look like "the count is zero" ──────

def test_missing_values_are_counted_and_labelled_not_dropped():
    rows = [_rec(outcome_category=None), _rec(outcome_category=None),
            _rec(outcome_category="Guest error")]
    out = run_query(rows, ["outcome_category"], ["count"])
    assert out["unset"]["outcome_category"] == 2       # said out loud
    keys = {r["key"][0] for r in out["rows"]}
    assert UNSET in keys                               # and visible as a row
    assert sum(r["values"]["count"] for r in out["rows"]) == 3   # nothing dropped


def test_unnested_dimensions_are_declared():
    rows = [_rec(scenarios=["Refund issues", "Content issues"]),
            _rec(scenarios=["Refund issues"])]
    out = run_query(rows, ["scenarios"], ["count"])
    assert out["unnested"] == ["scenarios"]            # so a reader knows why
    counts = {r["key"][0]: r["values"]["count"] for r in out["rows"]}
    assert counts == {"Refund issues": 2, "Content issues": 1}
    # 3 counted across 2 reviews — legitimate, and the caller was told.
    assert sum(counts.values()) > out["matched"]


# ── totals are computed, never summed ───────────────────────────────────────

def test_totals_are_over_all_rows_not_a_sum_of_groups():
    # Two groups of one. Summing "Solved %" would give 100, and summing medians is
    # meaningless; the totals must be recomputed over every matching record.
    rows = [_rec(tier="Tier 1", solved=True, tts_hours=10.0),
            _rec(tier="Tier 2", solved=False, tts_hours=30.0)]
    out = run_query(rows, ["tier"], ["solved_pct", "median_tts", "count"])
    assert out["totals"]["solved_pct"] == 50.0
    assert out["totals"]["median_tts"] == 20.0
    assert out["totals"]["count"] == 2


def test_median_not_mean_for_time_to_send():
    # One late backlog item must not drag the headline: mean would be ~130.
    rows = [_rec(tts_hours=v) for v in (10.0, 20.0, 30.0, 500.0)]
    out = run_query(rows, [], ["median_tts"])
    assert out["totals"]["median_tts"] == 25.0


def test_measures_are_none_not_zero_when_nothing_to_measure():
    # No timed review is "we cannot say", not "zero hours".
    out = run_query([_rec(tts_hours=None, rating=None)], [], ["median_tts", "avg_rating", "count"])
    assert out["totals"]["median_tts"] is None
    assert out["totals"]["avg_rating"] is None
    assert out["totals"]["count"] == 1


# ── filters, limits, sorting, errors ────────────────────────────────────────

def test_filters_are_exact_and_blank_means_any():
    rows = [_rec(tier="Tier 1"), _rec(tier="Tier 2"), _rec(tier="Tier 2")]
    assert run_query(rows, [], ["count"], filters={"tier": "Tier 2"})["matched"] == 2
    assert run_query(rows, [], ["count"], filters={"tier": ""})["matched"] == 3
    assert run_query(rows, [], ["count"], filters={"tier": "Nope"})["matched"] == 0


def test_filter_matches_inside_a_multi_value_field():
    rows = [_rec(scenarios=["Refund issues", "Content issues"]), _rec(scenarios=["Other"])]
    assert run_query(rows, [], ["count"], filters={"scenarios": "Content issues"})["matched"] == 1


def test_limit_truncates_and_says_so():
    rows = [_rec(vendor=f"V{i}") for i in range(10)]
    out = run_query(rows, ["vendor"], ["count"], limit=3)
    assert len(out["rows"]) == 3
    assert out["row_count"] == 10 and out["truncated"] is True


def test_sorts_by_the_named_measure():
    rows = [_rec(vendor="A"), _rec(vendor="B"), _rec(vendor="B")]
    desc = run_query(rows, ["vendor"], ["count"], sort="count")
    assert desc["rows"][0]["key"] == ["B"]
    asc = run_query(rows, ["vendor"], ["count"], sort="count", descending=False)
    assert asc["rows"][0]["key"] == ["A"]


def test_unknown_field_names_a_clear_error():
    # An error should say what would work, not fail silently or return empty.
    with pytest.raises(ValueError, match="unknown dimension"):
        run_query([_rec()], ["not_a_field"], ["count"])
    with pytest.raises(ValueError, match="unknown measure"):
        run_query([_rec()], ["tier"], ["not_a_measure"])


def test_registry_covers_every_dimension_and_measure():
    reg = field_registry()
    assert {d["key"] for d in reg["dimensions"]} == {d.key for d in DIMENSIONS}
    assert {m["key"] for m in reg["measures"]} == set(MEASURES)
    # every dimension belongs to a declared view, so the picker can group them
    assert all(d["view"] in reg["views"] for d in reg["dimensions"])


def test_values_of_unnests_and_reports_absence():
    assert values_of({"scenarios": ["a", "b"]}, "scenarios") == ["a", "b"]
    assert values_of({"vendor": "V"}, "vendor") == ["V"]
    for empty in (None, "", []):
        assert values_of({"vendor": empty}, "vendor") == []


# ── against the real schema ─────────────────────────────────────────────────

def _n(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


def test_project_reads_the_real_models(live_db):
    from server.db import Review, RcaDraft
    rec_at = datetime(2026, 9, 1, 8, 0)
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="rp", received_at=_n(rec_at), status="sent", rating=1,
                     language="German", picked_up_by="Test", sent_route="rca_posted"))
        s.add(RcaDraft(id="rp-d", review_id="rp", match_tier=1, l1="Operations Issue",
                       l2="Ticket Issues", scenarios=["Invalid tickets"],
                       zendesk_ticket_ids=["1", "2"], booking={"id": "B1", "vendorName": "Acme"},
                       sent_at=_n(rec_at + timedelta(hours=6))))
        s.commit()
        r = s.query(Review).filter_by(id="rp").one()
        d = s.query(RcaDraft).filter_by(review_id="rp").one()
        rec = project(r, d)
    finally:
        s.close()
    assert rec["tier"] == "Tier 1"
    assert rec["owner"] == UNASSIGNED          # the test account is not a person
    assert rec["traceable"] == "Traceable"     # a booking id was found
    assert rec["solved"] is True
    assert rec["tts_hours"] == 6.0             # received -> sent, in hours
    assert rec["zd_count"] == 2
    assert rec["scenarios"] == ["Invalid tickets"]
    assert rec["vendor"] == "Acme"


def test_records_respects_the_date_window(live_db):
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="in1", received_at=datetime(2026, 9, 5), status="new", rating=1))
        s.add(Review(id="edge_start", received_at=datetime(2026, 9, 1), status="new", rating=1))
        s.add(Review(id="edge_end", received_at=datetime(2026, 9, 10), status="new", rating=1))
        s.add(Review(id="before", received_at=datetime(2026, 8, 31), status="new", rating=1))
        s.commit()
        got = records(s, datetime(2026, 9, 1), datetime(2026, 9, 10))
    finally:
        s.close()
    ids = {r["review_id"] for r in got}
    # start inclusive, end exclusive
    assert ids == {"in1", "edge_start"}


def test_a_review_with_no_booking_is_not_traceable(live_db):
    """The NEGATIVE direction. Mutation testing caught that every traceability
    assertion pointed the same way, so hard-coding "Traceable" passed the suite."""
    from server.db import Review
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="nb", received_at=datetime(2026, 9, 1), status="new", rating=1))
        s.commit()
        rec = project(s.query(Review).filter_by(id="nb").one(), None)
    finally:
        s.close()
    assert rec["traceable"] == "No booking found"
    assert rec["tier"] == "Untraceable"          # no booking, no tier
    assert run_query([rec], [], ["traced_pct"])["totals"]["traced_pct"] == 0.0


# ── the new dimensions and the calculated takedown ──────────────────────────

def test_takedown_uncertain_when_the_rca_ran_but_left_it_blank():
    """A CALCULATED verdict: RCA ran (a prompt version is stamped) but produced
    no takedown -> Uncertain, a real state. No RCA -> (not set). The two must
    not read the same (CLAUDE.md rule 1)."""
    from server.services.reporting_query import _takedown_value
    assert _takedown_value("Yes", "rca_v4+abc") == "Yes"
    assert _takedown_value("", "rca_v4+abc") == "Uncertain"     # ran, undecided
    assert _takedown_value(None, "rca_v4+abc") == "Uncertain"
    assert _takedown_value("", "") is None                      # never ran
    assert _takedown_value(None, None) is None


def test_new_dimensions_are_in_the_picker():
    dims = {d["key"]: d for d in field_registry()["dimensions"]}
    assert dims["booking_date"]["view"] == "Booking details"
    assert dims["visit_date"]["view"] == "Booking details"


def test_new_dimensions_project_from_the_real_models(live_db):
    from server.db import Review, RcaDraft
    rec_at = datetime(2026, 9, 1, 8, 0)
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="nd", received_at=_n(rec_at), status="sent", rating=1,
                     language="German", picked_up_by="Avi",
                     sent_route="rca_posted"))
        s.add(RcaDraft(id="nd-d", review_id="nd", match_tier=1,
                       rca_prompt_version="rca_v4+abc",
                       booking={"id": "B1", "date_of_booking": "2026-07-15 02:15:00",
                                "date_of_visit": "2026-07-23"},
                       sent_at=_n(rec_at + timedelta(hours=6))))
        s.commit()
        rec = project(s.query(Review).filter_by(id="nd").one(),
                      s.query(RcaDraft).filter_by(review_id="nd").one())
    finally:
        s.close()
    # dates as their day only, not the timestamp
    assert rec["booking_date"] == "2026-07-15"
    assert rec["visit_date"] == "2026-07-23"
    # RCA ran (prompt version present) but no takedown verdict -> Uncertain
    assert rec["takedown"] == "Uncertain"
