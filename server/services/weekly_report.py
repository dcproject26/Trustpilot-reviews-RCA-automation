"""The weekly ORM digest — a Monday-8pm→Monday-8pm IST week, on demand.

Same source of truth and same READ-ONLY, compute-on-the-fly design as the daily
digest (`daily_report.py`): a few group-by passes over the reviews + rca_drafts
already in Postgres, NO snapshot table. A week is just a wider window over the
same `received_at` / `sent_at` / `closed_at` columns, so any week can be rebuilt
exactly from the durable rows — even if a review was edited after the fact.

The week is anchored to Monday 20:00 IST so that SEVEN daily 8pm→8pm windows nest
EXACTLY inside one week: the 7 daily "Received" counts sum to the weekly one, and
each day in the trend matches what that day's daily report showed. `weeks_ago`
selects a completed week (0 = the most recently completed one), which is what the
dashboard's week picker sends.

Three layers, testable without a database, exactly as the daily report:
  * `summarize(...)` / `render_weekly(...)` — pure.
  * `build_weekly_digest(db, now, weeks_ago)` — the DB-facing glue.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from server.services.daily_report import (
    IST, REPORT_HOUR_IST, Summary, summarize, _section, _DIV,
    _TIER_FIXED, _TIER_DOT, _collect_solved_between, _received_between)

WEEK = timedelta(days=7)


def _week_end_ist(now: datetime, weeks_ago: int = 0) -> datetime:
    """The Monday-20:00-IST cutoff that CLOSES the selected week.

    weeks_ago=0 is the most recently COMPLETED week: the last Monday 20:00 IST at
    or before `now`. Before Monday 8pm this is last Monday; from Monday 8pm on it
    is today. Each extra `weeks_ago` steps the cutoff back a further 7 days."""
    now_ist = now.astimezone(IST)
    anchor = now_ist.replace(hour=REPORT_HOUR_IST, minute=0, second=0, microsecond=0)
    anchor -= timedelta(days=anchor.weekday())        # back to THIS week's Monday 8pm
    if anchor > now_ist:                              # this Monday 8pm not reached yet
        anchor -= WEEK
    return anchor - WEEK * weeks_ago


def week_bounds(now: datetime, weeks_ago: int = 0) -> tuple[datetime, datetime]:
    """(start, end) of the selected Mon-8pm→Mon-8pm IST week, as UTC-aware."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    end_ist = _week_end_ist(now, weeks_ago)
    start_ist = end_ist - WEEK
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)


def _week_db_bounds(now: datetime, weeks_ago: int = 0) -> tuple[datetime, datetime]:
    """The week as NAIVE UTC, to compare against the DB's naive columns — the same
    tz-stripping the daily report does (see daily_report._db_bounds)."""
    start, end = week_bounds(now, weeks_ago)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _day_label(day_end_utc_naive: datetime) -> str:
    """A trend row is one nested 8pm→8pm day, labelled by its END date in IST —
    the SAME convention the daily report's date_label uses, so a trend row and
    that day's daily report carry the same date. "9 Sep" (no leading zero)."""
    end_ist = day_end_utc_naive.replace(tzinfo=timezone.utc).astimezone(IST)
    return f"{end_ist.strftime('%a')} {end_ist.strftime('%d %b').lstrip('0')}"


def build_weekly_digest(db, now: datetime | None = None, weeks_ago: int = 0) -> str:
    """The full weekly digest text for the selected completed week."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    start, end = _week_db_bounds(now, weeks_ago)

    solved_rows = _collect_solved_between(db, start, end)
    summary = summarize(solved_rows, _received_between(db, start, end))

    # Per-day trend: the 7 nested 8pm→8pm windows, in order. Each is one exact
    # daily window, so these rows sum back to the weekly Received/Solved.
    trend: list[tuple[str, int, int]] = []
    for i in range(7):
        d_start = start + timedelta(days=i)
        d_end = start + timedelta(days=i + 1)
        rec = _received_between(db, d_start, d_end)
        solv = len(_collect_solved_between(db, d_start, d_end))
        trend.append((_day_label(d_end), rec, solv))

    a, b = week_bounds(now, weeks_ago)
    a_ist, b_ist = a.astimezone(IST), b.astimezone(IST)
    _d = lambda t: t.strftime("%d %b").lstrip("0")
    title_label = f"{_d(a_ist)} – {b_ist.strftime('%d %b %Y').lstrip('0')}"
    window_label = f"Mon {_d(a_ist)} 8pm → Mon {_d(b_ist)} 8pm IST"
    return render_weekly(summary, title_label, window_label, trend)


def render_weekly(summary: Summary, title_label: str, window_label: str,
                  trend: list[tuple[str, int, int]]) -> str:
    """Pure: a Summary + the per-day trend -> the Slack text. Mirrors the daily
    digest's blocks (tier / Solved by / categories) with a weekly headline and a
    daily trend on top, and reuses the daily section/divider helpers so the two
    reports look like one family."""
    s = summary
    out = [f"📅  *ORM Weekly — {title_label}*"]
    out.append(f"Received: *{s.received}*   ·   Solved: *{s.solved}*")
    out.append(f"_{window_label}_")

    # Daily trend — received / solved per nested 8pm→8pm day, so a spike is
    # visible and the rows sum back to the weekly totals.
    trend_rows = [f"• {label} — {rec} / {solv}" for label, rec, solv in trend]
    out += _section("*📈  Daily trend — received / solved*", trend_rows)

    tier_rows = [f"{_TIER_DOT.get(lbl, '•')} {lbl} — {s.tier.get(lbl, 0)}"
                 for lbl in _TIER_FIXED]
    out += _section("*🏷️  Reviews by tier*", tier_rows)

    if s.people:
        out += _section("*🧑‍💻  Solved by*",
                        [f"• {k} — {v}" for k, v in s.people])

    if s.categories:
        out += _section("*📂  Top issue categories (L1 / L2)*",
                        [f"• {k} — {v}" for k, v in s.categories])

    return "\n".join(out)


def week_options(now: datetime | None = None, count: int = 8) -> list[dict]:
    """The dashboard week picker's choices: the last `count` COMPLETED weeks,
    newest first, as {weeks_ago, label}. Label is the week's date span, matching
    the digest title so the picked option and the report agree."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    _d = lambda t: t.strftime("%d %b").lstrip("0")
    opts = []
    for w in range(count):
        a, b = week_bounds(now, w)
        a_ist, b_ist = a.astimezone(IST), b.astimezone(IST)
        label = f"{_d(a_ist)} – {b_ist.strftime('%d %b %Y').lstrip('0')}"
        opts.append({"weeks_ago": w, "label": label})
    return opts
