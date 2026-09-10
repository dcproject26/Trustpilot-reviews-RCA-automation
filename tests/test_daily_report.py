"""The daily ORM digest: the arithmetic and the text, plus the 24h window.

Most of this is driven against the pure functions (summarize / render_digest /
_tier_label) with hand-built cohorts — no database — so the counting logic is
tested directly. One test exercises collect_rows against the real schema to pin
the rolling-24h window (the whole reason the digest is not "today so far").
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.services.daily_report import (
    Row, Summary, summarize, render_digest, _tier_label, collect_rows,
    build_daily_digest, IST)
from server.tiers import UNTRACEABLE, IDENTIFIED, PROCESSING, CANDIDATES, SENT


# ── tier label: T1 / T2 / Untraceable / Pending ─────────────────────────────

def test_tier_label_uses_authoritative_untraceable_not_just_missing_tier():
    # Untraceable is the classify() bucket — a search that found nothing.
    assert _tier_label(Row(bucket=UNTRACEABLE, tier=None)) == "Untraceable"
    # match_tier gives T1 / T2.
    assert _tier_label(Row(bucket=IDENTIFIED, tier=1)) == "T1"
    assert _tier_label(Row(bucket=IDENTIFIED, tier=2)) == "T2"
    # No tier yet and NOT untraceable (still processing / shortlist pending) is
    # Pending — it must NOT read as untraceable just because tier is None.
    assert _tier_label(Row(bucket=PROCESSING, tier=None)) == "Pending"
    assert _tier_label(Row(bucket=CANDIDATES, tier=None)) == "Pending"


# ── summarize ───────────────────────────────────────────────────────────────

def _cohort():
    return [
        Row(status="sent", solved=True,  picked_up_by="Avi",      bucket=IDENTIFIED, tier=1, l1="Operations", l2="Ticket Issues"),
        Row(status="sent", solved=True,  picked_up_by="Avi",      bucket=IDENTIFIED, tier=2, l1="Operations", l2="Ticket Issues"),
        Row(status="sent", solved=True,  picked_up_by="Swagatom", bucket=UNTRACEABLE, tier=None, l1="Experience", l2="Guide"),
        Row(status="draft", solved=False, picked_up_by="",        bucket=PROCESSING, tier=None),
    ]


def test_received_and_solved_counts():
    s = summarize(_cohort())
    assert s.received == 4
    assert s.solved == 3


def test_tier_counts_sum_back_to_received():
    s = summarize(_cohort())
    assert s.tier == {"T1": 1, "T2": 1, "Untraceable": 1, "Pending": 1}
    assert sum(s.tier.values()) == s.received      # nothing dropped silently


def test_people_biggest_first_and_unassigned_last():
    # "Zoe" sorts AFTER "Unassigned" alphabetically, so if Unassigned were not
    # forced last it would tie-break ahead of Zoe. It must not.
    cohort = [
        Row(picked_up_by="Avi"), Row(picked_up_by="Avi"),
        Row(picked_up_by="Zoe"),
        Row(picked_up_by=""),          # -> Unassigned
    ]
    s = summarize(cohort)
    assert s.people == [("Avi", 2), ("Zoe", 1), ("Unassigned", 1)]


def test_categories_counted_and_sorted():
    s = summarize(_cohort())
    assert s.categories[0] == ("Operations / Ticket Issues", 2)
    assert ("Experience / Guide", 1) in s.categories


def test_empty_cohort_has_no_divide_by_zero():
    s = summarize([])
    assert s.received == 0 and s.solved == 0
    # render must not raise and must show 0%.
    assert "*0%*" in render_digest(s, "7 Sep 2026")


# ── render ──────────────────────────────────────────────────────────────────

def test_render_headline_and_blocks():
    out = render_digest(summarize(_cohort()), "10 Sep 2026")
    assert out.startswith("*ORM Daily — 10 Sep 2026*")
    assert "*4 in*" in out and "*3 solved*" in out and "*75%*" in out
    assert "*Tier*" in out
    assert "Untraceable" in out
    assert "*Picked up by*" in out and "Avi" in out
    assert "*Top categories*" in out


def test_pending_row_hidden_when_zero_shown_when_present():
    # A cohort with no Pending review must not print a Pending row...
    no_pending = [Row(bucket=IDENTIFIED, tier=1, solved=True)]
    assert "Pending" not in render_digest(summarize(no_pending), "x")
    # ...but a real Pending review must appear (never silently absorbed).
    with_pending = no_pending + [Row(bucket=PROCESSING, tier=None)]
    assert "Pending" in render_digest(summarize(with_pending), "x")


def test_untraceable_labelled_not_t3():
    out = render_digest(summarize(_cohort()), "x")
    assert "Untraceable" in out
    assert "T3" not in out


# ── the 24h window, against the real schema ─────────────────────────────────

def _add_review(db, rid, received_at, status="new"):
    from server.db import Review
    db.add(Review(id=rid, received_at=received_at, status=status, rating=1))


def test_collect_rows_is_a_rolling_24h_window(live_db):
    now = datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc)   # 8pm IST
    s = live_db.SessionLocal()
    try:
        _add_review(s, "in_1h",   now - timedelta(hours=1))
        _add_review(s, "in_23h",  now - timedelta(hours=23))
        _add_review(s, "edge_24h", now - timedelta(hours=24))    # inclusive edge
        _add_review(s, "old_25h", now - timedelta(hours=25))     # excluded
        _add_review(s, "future",  now + timedelta(hours=1))      # excluded
        _add_review(s, "no_date", None)                          # excluded
        s.commit()
        rows = collect_rows(s, now)
    finally:
        s.close()
    # 3 in window: in_1h, in_23h, edge_24h. The 25h-old, the future one, and the
    # one with no received_at are all out.
    assert len(rows) == 3


def test_build_daily_digest_dates_in_ist(live_db):
    # 18:00 UTC on 9 Sep is 23:30 IST on 9 Sep — the label is the IST date.
    now = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
    s = live_db.SessionLocal()
    try:
        text = build_daily_digest(s, now)
    finally:
        s.close()
    assert now.astimezone(IST).strftime("%d %b %Y").lstrip("0") in text
    assert "9 Sep 2026" in text
