"""The weekly ORM digest: the Monday-8pm week window, the nesting property (7
daily windows sum to one week), the text, and the picker options.

Driven against the pure functions and, for the window, against the real schema —
the same split as test_daily_report.py.
"""
from datetime import datetime, timedelta, timezone

from server.services.daily_report import Row, summarize, IST, _received_between, \
    _collect_solved_between
from server.services.weekly_report import (
    week_bounds, _week_end_ist, _week_db_bounds, build_weekly_digest,
    render_weekly, week_options)


def _n(dt):
    return dt.replace(tzinfo=None) if dt is not None else None


# ── the Monday-8pm week window ──────────────────────────────────────────────

def test_week_is_monday_8pm_to_monday_8pm_ist():
    # Wed 16 Sep 2026, well after Monday 8pm -> the last completed week is
    # Mon 7 Sep 8pm .. Mon 14 Sep 8pm (14 Sep 2026 is a Monday).
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    start, end = week_bounds(now)
    assert end.astimezone(IST).strftime("%a %Y-%m-%d %H:%M") == "Mon 2026-09-14 20:00"
    assert start.astimezone(IST).strftime("%a %Y-%m-%d %H:%M") == "Mon 2026-09-07 20:00"


def test_before_monday_8pm_uses_the_previous_completed_week():
    # Monday 14 Sep 10:00 IST is BEFORE this Monday's 8pm, so the last COMPLETED
    # week ended the PREVIOUS Monday (7 Sep) 8pm.
    now = datetime(2026, 9, 14, 4, 30, tzinfo=timezone.utc)   # 10:00 IST Mon
    start, end = week_bounds(now)
    assert end.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-09-07 20:00"
    assert start.astimezone(IST).strftime("%Y-%m-%d %H:%M") == "2026-08-31 20:00"


def test_weeks_ago_steps_back_seven_days_each():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    e0 = _week_end_ist(now, 0)
    e1 = _week_end_ist(now, 1)
    e2 = _week_end_ist(now, 2)
    assert (e0 - e1).days == 7 and (e1 - e2).days == 7
    assert e0.weekday() == 0 and e1.weekday() == 0     # always a Monday


# ── the nesting property: 7 daily windows sum to the week ───────────────────

def test_seven_daily_windows_sum_to_the_week(live_db):
    # THE PROPERTY THIS GUARDS: the weekly Received/Solved equal the sum of the
    # seven nested 8pm→8pm days, so weekly and daily can never disagree.
    from server.db import Review, RcaDraft
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    start, end = _week_db_bounds(now)                  # naive UTC week bounds
    s = live_db.SessionLocal()
    try:
        # one review received AND solved on each of the 7 nested days (mid-day)
        for i in range(7):
            mid = start + timedelta(days=i, hours=5)
            rid = f"r{i}"
            s.add(Review(id=rid, received_at=_n(mid), status="sent", rating=1,
                         picked_up_by="Avi"))
            s.add(RcaDraft(id=rid + "-d", review_id=rid, match_tier=1,
                           sent_at=_n(mid), booking={"id": f"B{i}"}))
        # one OUTSIDE the week (before start) — must not be counted
        s.add(Review(id="before", received_at=_n(start - timedelta(hours=1)),
                     status="sent", rating=1, picked_up_by="Avi"))
        s.add(RcaDraft(id="before-d", review_id="before", match_tier=1,
                       sent_at=_n(start - timedelta(hours=1)), booking={"id": "BX"}))
        s.commit()
        week_recv = _received_between(s, start, end)
        week_solv = len(_collect_solved_between(s, start, end))
        day_recv = sum(_received_between(s, start + timedelta(days=i),
                                         start + timedelta(days=i + 1))
                       for i in range(7))
        day_solv = sum(len(_collect_solved_between(s, start + timedelta(days=i),
                                                   start + timedelta(days=i + 1)))
                       for i in range(7))
        text = build_weekly_digest(s, now)
    finally:
        s.close()
    assert week_recv == 7 and week_solv == 7           # the "before" one excluded
    assert day_recv == week_recv and day_solv == week_solv
    # The rendered digest's headline uses the week's own window, not 0/other.
    assert "Received: *7*" in text and "Solved: *7*" in text
    # Each nested day had exactly one received + one solved, so every trend row
    # reads "1 / 1" — pins the per-day window to the right day (an off-by-one
    # day offset would zero the rows).
    trend_rows = [ln for ln in text.splitlines()
                  if ln.startswith("• ") and " / " in ln]
    assert len(trend_rows) == 7
    assert all(ln.endswith("— 1 / 1") for ln in trend_rows)


# ── render ──────────────────────────────────────────────────────────────────

def _sample_summary():
    rows = [Row(solved=True, picked_up_by="Avi", tier=1, l1="Ops", l2="Delivery"),
            Row(solved=True, picked_up_by="Avi", tier=2, l1="Ops", l2="Delivery"),
            Row(solved=True, picked_up_by="Shruti", tier=None)]      # untraceable
    return summarize(rows, received_count=20)


def test_render_weekly_headline_trend_and_blocks():
    trend = [("Tue 8 Sep", 3, 2), ("Wed 9 Sep", 5, 4)]
    out = render_weekly(_sample_summary(), "7 – 14 Sep 2026",
                        "Mon 7 Sep 8pm → Mon 14 Sep 8pm IST", trend)
    assert "*ORM Weekly — 7 – 14 Sep 2026*" in out
    assert "Received: *20*" in out and "Solved: *3*" in out
    assert "Mon 7 Sep 8pm → Mon 14 Sep 8pm IST" in out
    assert "%" not in out
    # Daily trend rows, received / solved.
    assert "*📈  Daily trend — received / solved*" in out
    assert "• Tue 8 Sep — 3 / 2" in out and "• Wed 9 Sep — 5 / 4" in out
    # Same blocks as the daily report, so the two look like one family.
    assert "🟢 Tier 1 — 1" in out and "🔴 Untraceable — 1" in out
    assert "*🧑‍💻  Solved by*" in out and "• Avi — 2" in out
    assert "*📂  Top issue categories (L1 / L2)*" in out
    assert "━" in out


def test_build_weekly_digest_has_seven_trend_rows(live_db):
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    s = live_db.SessionLocal()
    try:
        text = build_weekly_digest(s, now)
    finally:
        s.close()
    assert "*ORM Weekly — 7 Sep – 14 Sep 2026*" in text   # matches the picker label
    assert "Mon 7 Sep 8pm → Mon 14 Sep 8pm IST" in text
    # Exactly seven nested days -> exactly seven trend rows (a "• Day — r / s"
    # line each). Counting precisely so dropping a day is caught.
    trend_rows = [ln for ln in text.splitlines()
                  if ln.startswith("• ") and " / " in ln]
    assert len(trend_rows) == 7


# ── the picker ──────────────────────────────────────────────────────────────

def test_week_options_are_completed_weeks_newest_first():
    now = datetime(2026, 9, 16, 6, 0, tzinfo=timezone.utc)
    opts = week_options(now, count=3)
    assert [o["weeks_ago"] for o in opts] == [0, 1, 2]
    assert opts[0]["label"] == "7 Sep – 14 Sep 2026"
    assert opts[1]["label"] == "31 Aug – 7 Sep 2026"
