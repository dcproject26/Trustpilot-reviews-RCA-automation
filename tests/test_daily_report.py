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
    collect_solved_today_rows, received_count, build_daily_digest,
    window_bounds, _today_bounds, IST)
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


# ── two cohorts: headline (8pm→8pm) vs Solved-by (whole calendar day) ────────

def test_solved_by_uses_its_own_wider_cohort():
    # Headline Solved, tiers and categories come from the 8pm→8pm `solved_rows`;
    # the per-person Solved-by comes from the wider `solved_by_rows` (everything
    # closed across the day, older reviews included). The two totals legitimately
    # differ — that is the whole point of the separate cohort.
    window = [Row(solved=True, picked_up_by="Avi", tier=1,
                  l1="Operations", l2="Ticket Issues")]
    today = [Row(solved=True, picked_up_by="Avi"),
             Row(solved=True, picked_up_by="Shruti"),
             Row(solved=True, picked_up_by="Shruti")]
    s = summarize(window, received_count=5, solved_by_rows=today)
    assert s.solved == 1                          # headline = window cohort
    assert sum(s.tier.values()) == 1              # tiers follow the window cohort
    assert s.categories == [("Operations / Ticket Issues", 1)]  # window cohort
    # People follow the wider today cohort, biggest-first, and its total (3) is
    # deliberately not equal to the headline Solved (1).
    assert s.people == [("Shruti", 2), ("Avi", 1)]
    assert sum(n for _, n in s.people) == 3


def test_solved_by_defaults_to_window_cohort_when_not_given():
    # Older callers/tests pass no solved_by_rows -> Solved-by == the window rows.
    s = summarize(_solved_cohort())
    assert s.people == [("Avi", 2), ("Swagatom", 1)]


def test_empty_cohort_renders_zeros_without_error():
    s = summarize([], received_count=0)
    assert s.received == 0 and s.solved == 0
    out = render_digest(s, "7 Sep 2026")
    assert "Received: *0*" in out and "Solved: *0*" in out


# ── render ──────────────────────────────────────────────────────────────────

def test_render_headline_and_blocks():
    out = render_digest(summarize(_solved_cohort(), received_count=10),
                        "10 Sep 2026", "8pm 9 Sep → 8pm 10 Sep IST")
    assert "*ORM Daily — 10 Sep 2026*" in out
    # The 8pm→8pm window is stamped so the team knows the period.
    assert "8pm 9 Sep → 8pm 10 Sep IST" in out
    # Two independent day-counts, NO cross-cohort ratio (Solved can exceed
    # Received on a catch-up day — a "130%" would be nonsense).
    assert "Received: *10*" in out
    assert "Solved: *3*" in out
    assert "%" not in out
    assert "*🏷️  Reviews by tier*" in out
    assert "🟢 Tier 1 — 1" in out and "🔴 Untraceable — 1" in out
    assert "*🧑‍💻  Solved by*" in out and "• Avi — 2" in out
    assert "*📂  Top issue categories (L1 / L2)*" in out
    # Section breakers present between the blocks.
    assert "━" in out


def test_solved_by_caption_rendered_and_totals_may_differ():
    s = summarize([Row(solved=True, picked_up_by="Avi", tier=1)], received_count=3,
                  solved_by_rows=[Row(solved=True, picked_up_by="Avi"),
                                  Row(solved=True, picked_up_by="Shruti")])
    out = render_digest(s, "13 Sep 2026",
                        "Received & solved · 8pm 12 Sep → 8pm 13 Sep IST",
                        "closed on 13 Sep, incl. older reviews")
    # Both frames labelled so the reader knows why the two "solved" numbers differ.
    assert "Received & solved · 8pm 12 Sep → 8pm 13 Sep IST" in out
    assert "closed on 13 Sep, incl. older reviews" in out
    assert "Solved: *1*" in out                       # headline = window cohort
    assert "• Avi — 1" in out and "• Shruti — 1" in out   # Solved-by = day cohort


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

def _n(dt):
    """Store as NAIVE UTC, exactly as production does (utcnow() /
    utcfromtimestamp(...).replace(tzinfo=None)). The window comparison is naive
    UTC too, so the tests exercise the real Postgres path rather than a
    tz-aware shape sqlite happens to tolerate."""
    return dt.replace(tzinfo=None) if dt is not None else None


def _add_review(db, rid, received_at, status="new"):
    from server.db import Review
    db.add(Review(id=rid, received_at=_n(received_at), status=status, rating=1))


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
        s.add(Review(id="old_but_solved", received_at=_n(old_arrival), status="sent",
                     rating=1, picked_up_by="Swagatom"))
        s.add(RcaDraft(id="old_but_solved-d", review_id="old_but_solved",
                       match_tier=1, sent_at=_n(in_win), booking={"id": "B1"}))
        # arrived and solved inside the window
        s.add(Review(id="same_day", received_at=_n(in_win), status="sent",
                     rating=1, picked_up_by="Paul"))
        s.add(RcaDraft(id="same_day-d", review_id="same_day", match_tier=2,
                       sent_at=_n(in_win), booking={"id": "B2"}))
        # sent, but finished OUTSIDE the window -> excluded
        s.add(Review(id="solved_yesterday", received_at=_n(old_arrival), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="solved_yesterday-d", review_id="solved_yesterday",
                       match_tier=1, sent_at=_n(now - timedelta(hours=30)),
                       booking={"id": "B3"}))
        s.commit()
        rows = collect_solved_rows(s, now)
        people = summarize(rows).people
    finally:
        s.close()
    # Swagatom (old arrival, solved today) AND Paul (same day) — NOT Avi.
    assert people == [("Paul", 1), ("Swagatom", 1)]


def test_solved_today_spans_the_calendar_day_not_the_8pm_window(live_db):
    # The Solved-by cohort is the report's IST CALENDAR DAY, capped at now. It
    # differs from the 8pm→8pm window at BOTH ends:
    #   * a review solved yesterday EVENING (inside the 8pm→8pm window, but before
    #     today 00:00) is in the window cohort but NOT the calendar day; and
    #   * a review solved AFTER today's 8pm cutoff but before the (delayed) run is
    #     in the calendar day but NOT the 8pm→8pm window.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST Sep10
    morning_today = datetime(2026, 9, 10, 4, 30, tzinfo=timezone.utc)   # 10:00 IST
    yesterday_eve = datetime(2026, 9, 9, 16, 30, tzinfo=timezone.utc)   # 22:00 IST Sep9
    after_cutoff  = datetime(2026, 9, 10, 15, 0, tzinfo=timezone.utc)   # 20:30 IST Sep10
    s = live_db.SessionLocal()
    try:
        s.add(Review(id="morning", received_at=_n(morning_today), status="sent",
                     rating=1, picked_up_by="Shruti"))
        s.add(RcaDraft(id="morning-d", review_id="morning", match_tier=1,
                       sent_at=_n(morning_today), booking={"id": "B1"}))
        s.add(Review(id="yest_eve", received_at=_n(yesterday_eve), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="yest_eve-d", review_id="yest_eve", match_tier=1,
                       sent_at=_n(yesterday_eve), booking={"id": "B2"}))
        s.add(Review(id="late", received_at=_n(morning_today), status="sent",
                     rating=1, picked_up_by="Devshree"))
        s.add(RcaDraft(id="late-d", review_id="late", match_tier=1,
                       sent_at=_n(after_cutoff), booking={"id": "B3"}))
        s.commit()
        today_people = summarize(collect_solved_today_rows(s, now)).people
        window_people = summarize(collect_solved_rows(s, now)).people
        text = build_daily_digest(s, now)
    finally:
        s.close()
    # Calendar day: morning + after-cutoff (Shruti, Devshree); NOT yesterday-eve.
    assert today_people == [("Devshree", 1), ("Shruti", 1)]
    # 8pm→8pm window: morning + yesterday-eve (Shruti, Avi); NOT the after-cutoff.
    assert window_people == [("Avi", 1), ("Shruti", 1)]
    # The digest's Solved-by must use the CALENDAR-DAY cohort, so Devshree (solved
    # after the 8pm cutoff) is credited and Avi (solved yesterday evening) is not.
    assert "• Devshree — 1" in text and "• Shruti — 1" in text
    assert "• Avi" not in text


def test_today_bounds_is_midnight_to_now_in_ist():
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST Sep10
    start, end = _today_bounds(now)                              # naive UTC
    # start = 10 Sep 00:00 IST = 9 Sep 18:30 UTC; end capped at now (15:30 UTC).
    assert start == datetime(2026, 9, 9, 18, 30)
    assert end == datetime(2026, 9, 10, 15, 30)


def test_collect_solved_detects_declared_untraceable_over_a_tier(live_db):
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 10, 15, 30, tzinfo=timezone.utc)     # 21:00 IST
    at = now - timedelta(hours=2)
    s = live_db.SessionLocal()
    try:
        # (a) closed untraceable (closed_at in window) but a stale Tier 2 draft
        s.add(Review(id="closed_untr", received_at=_n(at), status="sent", rating=1,
                     closed_at=_n(at),
                     close_reason="Untraceable — asked the guest for a booking reference."))
        s.add(RcaDraft(id="closed_untr-d", review_id="closed_untr", match_tier=2,
                       booking={"id": "B1"}))
        # (b) marked untraceable off the shortlist; reply sent in window
        s.add(Review(id="marked_untr", received_at=_n(at), status="sent", rating=1))
        s.add(RcaDraft(id="marked_untr-d", review_id="marked_untr", match_tier=None,
                       sent_at=_n(at), match_method="Marked untraceable by associate"))
        # (c) a normal Tier 1, sent in window
        s.add(Review(id="real_t1", received_at=_n(at), status="sent", rating=1))
        s.add(RcaDraft(id="real_t1-d", review_id="real_t1", match_tier=1,
                       sent_at=_n(at), booking={"id": "B2"}))
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
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 9, 18, 0, tzinfo=timezone.utc)
    solved_today = datetime(2026, 9, 9, 6, 30, tzinfo=timezone.utc)   # 12:00 IST Sep9
    s = live_db.SessionLocal()
    try:
        # One review solved during the calendar day so the Solved-by section (and
        # its caption) actually renders.
        s.add(Review(id="r", received_at=_n(solved_today), status="sent",
                     rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="r-d", review_id="r", match_tier=1,
                       sent_at=_n(solved_today), booking={"id": "B1"}))
        s.commit()
        text = build_daily_digest(s, now)
    finally:
        s.close()
    assert now.astimezone(IST).strftime("%d %b %Y").lstrip("0") in text
    assert "9 Sep 2026" in text
    # The 8pm->8pm window is stamped: this run (23:30 IST 9 Sep) covers
    # 8pm 8 Sep -> 8pm 9 Sep IST.
    assert "8pm 8 Sep → 8pm 9 Sep IST" in text
    # Both frames are labelled: the 8pm→8pm one for Received & Solved, and the
    # calendar-day one for the Solved-by credit.
    assert "Received & solved · 8pm 8 Sep → 8pm 9 Sep IST" in text
    assert "closed on 9 Sep, incl. older reviews" in text
