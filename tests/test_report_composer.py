"""The Slack report the Reporting page sends: the window presets and the composed
text. Driven against `compose`/`window_for` with hand-built records, so the wording
and the arithmetic are both tested without a database."""
from datetime import datetime

import pytest

from server.services.report_composer import (
    compose, window_for, build, DIVIDER)


def _rec(**kw):
    base = dict(review_id="r", date="2027-03-01", rating=1, language="English",
                status="sent", experience=None, vendor=None, tgid=None,
                fulfilment_type=None, booking_status=None, tier="Tier 1",
                traceable="Traceable", match_method="none", l1="Operations Issue",
                l2="Ticket Issues", sub_themes=[], scenarios=[],
                overlay_scenarios=[], claim_accuracy=[], resolution_given="No",
                takedown=None, outcome_category=None, dss_followed=None,
                owner="Avi", sent_route=None, close_reason=None, prompt_version=None,
                solved=True, posted=True, issue_count=2, zd_count=1, flag_count=0,
                tts_hours=10.0)
    base.update(kw)
    return base


# ── windows ─────────────────────────────────────────────────────────────────

def test_daily_is_one_day_and_weekly_is_seven():
    assert window_for("daily", None, None, today="2027-03-10") == ("2027-03-10", "2027-03-10")
    assert window_for("weekly", None, None, today="2027-03-10") == ("2027-03-04", "2027-03-10")


def test_custom_uses_the_dates_given():
    assert window_for("custom", "2027-03-01", "2027-03-05") == ("2027-03-01", "2027-03-05")


def test_custom_without_a_start_names_what_would_work():
    with pytest.raises(ValueError, match="preset='weekly'"):
        window_for("custom", None, "2027-03-05")


# ── the composed text ───────────────────────────────────────────────────────

def test_headline_counts_and_window():
    rows = [_rec(solved=True, posted=True), _rec(solved=False, posted=False)]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07", sections=[])
    assert "*ORM Weekly*" in txt
    assert "Received: *2*" in txt and "Solved: *1*" in txt and "RCAs posted: *1*" in txt
    assert "1 Mar → 7 Mar IST" in txt


def test_daily_shows_a_single_date_not_a_range():
    txt = compose([_rec()], "daily", "2027-03-01", "2027-03-01", sections=[])
    assert "_1 Mar IST_" in txt and "→" not in txt.split("\n")[2]


def test_sections_can_be_any_dimension():
    rows = [_rec(vendor="Acme"), _rec(vendor="Acme"), _rec(vendor="Zeta")]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07", sections=["vendor"])
    assert "*Vendor / supply partner*" in txt
    assert "• Acme — 2" in txt and "• Zeta — 1" in txt


def test_sections_can_be_ranked_by_another_measure():
    rows = [_rec(owner="Slow", tts_hours=100.0), _rec(owner="Fast", tts_hours=2.0)]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07",
                  sections=["owner"], rank_by="median_tts")
    assert "_by median time to send (h)_" in txt
    assert txt.index("• Slow — 100.0h") < txt.index("• Fast — 2.0h")   # ranked


def test_unrecorded_values_are_declared_not_hidden():
    # Rule 1: a short list because nobody filled the field is not the same as a
    # short list because the work did not happen.
    rows = [_rec(outcome_category=None), _rec(outcome_category=None),
            _rec(outcome_category="Guest error")]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07", sections=["outcome_category"])
    assert "2 review(s) have no outcome category — not counted above." in txt


def test_truncation_is_declared():
    rows = [_rec(vendor=f"V{i}") for i in range(9)]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07", sections=["vendor"], top=3)
    assert "_Top 3 of 9._" in txt
    assert len([l for l in txt.split("\n") if l.startswith("• ")]) == 3


def test_filters_are_stated_on_the_report():
    # A filtered number must not read as the whole day's number.
    rows = [_rec(tier="Tier 1"), _rec(tier="Tier 2")]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07",
                  sections=[], filters={"tier": "Tier 1"})
    assert "Filtered to: Match tier = Tier 1" in txt
    assert "Received: *1*" in txt


def test_an_empty_window_says_so_rather_than_sending_a_shell():
    txt = compose([], "daily", "2027-03-01", "2027-03-01", sections=["tier"])
    assert "No reviews arrived in this window." in txt
    assert "Received: *0*" in txt


def test_median_time_to_send_is_always_reported():
    rows = [_rec(tts_hours=10.0), _rec(tts_hours=30.0)]
    txt = compose(rows, "weekly", "2027-03-01", "2027-03-07", sections=[])
    assert "*Median time to send* — 20.0h" in txt


def test_nothing_timed_reads_as_unknown_not_zero():
    txt = compose([_rec(tts_hours=None)], "daily", "2027-03-01", "2027-03-01", sections=[])
    assert "*Median time to send* — —" in txt


def test_unknown_section_or_measure_is_a_clear_error():
    with pytest.raises(ValueError, match="unknown section"):
        compose([_rec()], "daily", "2027-03-01", "2027-03-01", sections=["nope"])
    with pytest.raises(ValueError, match="unknown measure"):
        compose([_rec()], "daily", "2027-03-01", "2027-03-01", rank_by="nope")


def test_divider_separates_the_blocks():
    txt = compose([_rec()], "weekly", "2027-03-01", "2027-03-07", sections=["tier"])
    assert DIVIDER in txt


# ── against the real schema ─────────────────────────────────────────────────

def test_build_reads_the_database_for_its_window(live_db):
    from server.db import Review, RcaDraft
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="w1", received_at=datetime(2027, 4, 2, 9), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="w1-d", review_id="w1", match_tier=1,
                       booking={"id": "B1"}, sent_at=datetime(2027, 4, 2, 15)))
        s.add(Review(id="w2", received_at=datetime(2027, 4, 9, 9), status="new", rating=1))
        s.commit()
        got = build(s, preset="daily", date_to="2027-04-02", sections=["tier"])
    finally:
        s.close()
    # date_to is INCLUSIVE, so the 2 Apr review is in and the 9 Apr one is not.
    assert got["date_from"] == "2027-04-02" and got["date_to"] == "2027-04-02"
    assert got["reviews"] == 1
    assert "Received: *1*" in got["text"]
    assert "• Tier 1 — 1" in got["text"]
