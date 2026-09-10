"""The daily ORM digest: the arithmetic and the text, plus the 24h window.

Most of this is driven against the pure functions (summarize / render_digest /
_tier_label) with hand-built cohorts — no database — so the counting logic is
tested directly. One test exercises collect_rows against the real schema to pin
the rolling-24h window (the whole reason the digest is not "today so far").
"""
from datetime import datetime, timedelta, timezone

import pytest

from server.services.daily_report import (
    Row, Summary, summarize, render_digest, _tier_label, collect_solved_rows,
    received_count, build_daily_digest, window_bounds, IST)
# ── tier label: Tier 1 / Tier 2 / Untraceable (the only three tracked) ──────

def test_tier_label_is_three_buckets_only():
    assert _tier_label(Row(tier=1)) == "Tier 1"
    assert _tier_label(Row(tier=2)) == "Tier 2"
    # Anything without a confirmed tier is Untraceable — regardless of sent
    # status, and with no invented "in progress" bucket (not a tracked state).
    assert _tier_label(Row(tier=None)) == "Untraceable"
    assert _tier_label(Row(tier=None, solved=True)) == "Untraceable"


def test_declared_untraceable_overrides_a_tentative_tier():
    # A review a person closed/marked untraceable is Untraceable even if a stale
    # tentative match_tier is still on the draft.
    assert _tier_label(Row(tier=2, declared_untraceable=True)) == "Untraceable"
    assert _tier_label(Row(tier=1, declared_untraceable=True)) == "Untraceable"


# ── summarize ───────────────────────────────────────────────────────────────
# Every row passed to summarize is a review SOLVED in the window; `received` is
# a separate context count.

def _solved_cohort():
    return [
        Row(solved=True, picked_up_by="Avi",      tier=1, l1="Operations", l2="Ticket Issues"),
        Row(solved=True, picked_up_by="Avi",      tier=2, l1="Operations", l2="Ticket Issues"),
        Row(solved=True, picked_up_by="Swagatom", tier=None, l1="Experience", l2="Guide"),  # untraceable
    ]


def test_received_and_solved_counts():
    s = summarize(_solved_cohort(), received_count=10)
    assert s.received == 10           # context number, its own cohort
    assert s.solved == 3              # len of the solved rows


def test_tier_counts_sum_back_to_solved():
    s = summarize(_solved_cohort())
    assert s.tier == {"Tier 1": 1, "Tier 2": 1, "Untraceable": 1}
    assert sum(s.tier.values()) == s.solved        # nothing dropped silently


def test_people_sum_to_solved_and_show_everyone():
    # Every solver is credited — this is the whole cohort of solved reviews, not
    # just those that also arrived today (the bug where only Paul showed).
    s = summarize(_solved_cohort())
    assert s.people == [("Avi", 2), ("Swagatom", 1)]
    assert sum(n for _, n in s.people) == s.solved


def test_people_biggest_first_and_unassigned_last():
    # "Zoe" sorts AFTER "Unassigned" alphabetically, so if Unassigned were not
    # forced last it would tie-break ahead of Zoe. It must not.
    cohort = [
        Row(solved=True, picked_up_by="Avi"), Row(solved=True, picked_up_by="Avi"),
        Row(solved=True, picked_up_by="Zoe"),
        Row(solved=True, picked_up_by=""),          # -> Unassigned
    ]
    s = summarize(cohort)
    assert s.people == [("Avi", 2), ("Zoe", 1), ("Unassigned", 1)]


def test_categories_counted_and_sorted():
    s = summarize(_solved_cohort())
    assert s.categories[0] == ("Operations / Ticket Issues", 2)
    assert ("Experience / Guide", 1) in s.categories


def test_empty_cohort_renders_zeros_without_error():
    s = summarize([], received_count=0)
    assert s.received == 0 and s.solved == 0
    out = render_digest(s, "7 Sep 2026")
    assert "Received today: *0*" in out and "Solved today: *0*" in out


# ── render ──────────────────────────────────────────────────────────────────

def test_render_headline_and_blocks():
    out = render_digest(summarize(_solved_cohort(), received_count=10), "10 Sep 2026")
    assert "*ORM Daily — 10 Sep 2026*" in out
    # Two independent day-counts, NO cross-cohort ratio (Solved can exceed
    # Received on a catch-up day — a "130%" would be nonsense).
    assert "Received today: *10*" in out
    assert "Solved today: *3*" in out
    assert "%" not in out
    assert "*🏷️  Reviews by tier*" in out
    assert "🟢 Tier 1 — 1" in out and "🔴 Untraceable — 1" in out
    assert "*🧑‍💻  Solved by*" in out and "• Avi — 2" in out
    assert "*📂  Top issue categories (L1 / L2)*" in out
    # Section breakers present between the blocks.
    assert "━" in out


def test_untraceable_always_shown_even_at_zero():
    # A day with no untraceable reviews must STILL show the Untraceable row at
    # 0 — the exact "where is untraceable?" confusion. No "in progress" bucket.
    out = render_digest(summarize([Row(tier=1, solved=True)]), "x")
    assert "🟢 Tier 1 — 1" in out
    assert "🔴 Untraceable — 0" in out
    assert "In progress" not in out


def test_untraceable_labelled_not_t3():
    out = render_digest(summarize(_solved_cohort()), "x")
    assert "Untraceable" in out
    assert "T3" not in out


# ── the 24h window, against the real schema ─────────────────────────────────

def _add_review(db, rid, received_at, status="new"):
    from server.db import Review
    db.add(Review(id=rid, received_at=received_at, status=status, rating=1))


def test_solved_cohort_is_by_when_finished_not_when_received(live_db):
    # THE BUG THIS GUARDS: a review that ARRIVED before the window but was
    # SOLVED inside it must be credited to its solver. Received-cohort logic
    # dropped these, so only same-day arrivals showed under "Solved by".
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST
    in_win = now - timedelta(hours=2)                            # inside window
    old_arrival = now - timedelta(days=3)                        # arrived long ago
    s = live_db.SessionLocal()
    try:
        # arrived 3 days ago, but its reply was SENT inside the window
        s.add(Review(id="old_but_solved", received_at=old_arrival, status="sent",
                     rating=1, picked_up_by="Swagatom"))
        s.add(RcaDraft(id="old_but_solved-d", review_id="old_but_solved",
                       match_tier=1, sent_at=in_win, booking={"id": "B1"}))
        # arrived and solved inside the window
        s.add(Review(id="same_day", received_at=in_win, status="sent",
                     rating=1, picked_up_by="Paul"))
        s.add(RcaDraft(id="same_day-d", review_id="same_day", match_tier=2,
                       sent_at=in_win, booking={"id": "B2"}))
        # sent, but finished OUTSIDE the window -> excluded
        s.add(Review(id="solved_yesterday", received_at=old_arrival, status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="solved_yesterday-d", review_id="solved_yesterday",
                       match_tier=1, sent_at=now - timedelta(hours=30),
                       booking={"id": "B3"}))
        s.commit()
        rows = collect_solved_rows(s, now)
        people = summarize(rows).people
    finally:
        s.close()
    # Swagatom (old arrival, solved today) AND Paul (same day) — NOT Avi.
    assert people == [("Paul", 1), ("Swagatom", 1)]


def test_collect_solved_detects_declared_untraceable_over_a_tier(live_db):
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST
    at = now - timedelta(hours=2)
    s = live_db.SessionLocal()
    try:
        # (a) closed untraceable (closed_at in window) but a stale Tier 2 draft
        s.add(Review(id="closed_untr", received_at=at, status="sent", rating=1,
                     closed_at=at,
                     close_reason="Untraceable — asked the guest for a booking reference."))
        s.add(RcaDraft(id="closed_untr-d", review_id="closed_untr", match_tier=2,
                       booking={"id": "B1"}))
        # (b) marked untraceable off the shortlist; reply sent in window
        s.add(Review(id="marked_untr", received_at=at, status="sent", rating=1))
        s.add(RcaDraft(id="marked_untr-d", review_id="marked_untr", match_tier=None,
                       sent_at=at, match_method="Marked untraceable by associate"))
        # (c) a normal Tier 1, sent in window
        s.add(Review(id="real_t1", received_at=at, status="sent", rating=1))
        s.add(RcaDraft(id="real_t1-d", review_id="real_t1", match_tier=1,
                       sent_at=at, booking={"id": "B2"}))
        s.commit()
        labels = sorted(_tier_label(r) for r in collect_solved_rows(s, now))
    finally:
        s.close()
    assert labels == ["Tier 1", "Untraceable", "Untraceable"]


def test_window_is_anchored_to_8pm_ist_not_the_run_time():
    # 9:00pm IST on 10 Sep -> the window is yesterday-8pm .. today-8pm, i.e.
    # 9 Sep 20:00 IST .. 10 Sep 20:00 IST, regardless of the 1h drift past 8pm.
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)   # 21:00 IST
    start, end = window_bounds(now)
    assert end.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-10 20:00"
    assert start.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-09 20:00"


def test_window_before_8pm_uses_the_previous_completed_day():
    # 10:00am IST on 10 Sep is BEFORE today's 8pm, so the last COMPLETED day is
    # 8 Sep 8pm .. 9 Sep 8pm.
    now = datetime(2026, 9, 10, 4, 30, tzinfo=timezone.utc)    # 10:00 IST
    start, end = window_bounds(now)
    assert end.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-09 20:00"
    assert start.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-08 20:00"


def test_received_count_8pm_boundary_start_inclusive_end_exclusive(live_db):
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)   # 21:00 IST
    s = live_db.SessionLocal()
    try:
        # window is [9 Sep 20:00 IST, 10 Sep 20:00 IST)
        _add_review(s, "just_in",   datetime(2026, 9, 10, 14, 29, tzinfo=timezone.utc))  # 19:59 IST today
        _add_review(s, "end_edge",  datetime(2026, 9, 10, 14, 30, tzinfo=timezone.utc))  # 20:00 IST today -> NEXT day
        _add_review(s, "start_edge",datetime(2026, 9,  9, 14, 30, tzinfo=timezone.utc))  # 20:00 IST yesterday -> IN
        _add_review(s, "too_early", datetime(2026, 9,  9, 14, 29, tzinfo=timezone.utc))  # 19:59 IST yesterday -> OUT
        s.commit()
        n = received_count(s, now)
    finally:
        s.close()
    # just_in + start_edge are in; end_edge (==end) and too_early are out.
    assert n == 2


def test_received_count_is_an_8pm_to_8pm_window(live_db):
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
        n = received_count(s, now)
    finally:
        s.close()
    # 3 in window: in_1h, in_23h, edge_24h. The 25h-old, the future one, and the
    # one with no received_at are all out.
    assert n == 3


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
