"""The daily ORM digest posted to the reports channel at 8pm IST.

READ-ONLY, and cheap: a few group-by passes over rows already in Postgres
(`reviews` + their `rca_drafts`). It runs NO BigQuery lookup and NO model call —
those happen only inside a review's own pipeline run; this just reads what those
runs already wrote.

The window is the LAST 24 HOURS ending at the run time, not "today so far". An
8pm "today" digest would silently miss every review that arrived between 8pm and
midnight — the run happens before the day is over. A rolling 24h window counts
every review exactly once across consecutive daily runs, with no gap and no
overlap.

Three layers, so the arithmetic is testable without a database:
  * `summarize(rows)`      — pure: counts -> a Summary.
  * `render_digest(...)`   — pure: a Summary -> the Slack text.
  * `collect_rows(db, now)`/`build_daily_digest(db, now)` — the DB-facing glue.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from server.tiers import SENT

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW = timedelta(hours=24)
# The daily report covers a FIXED 8pm→8pm IST day, not a rolling 24h ending at
# whatever moment the job fires. Anchoring to 20:00 IST means a scheduler that
# runs a few minutes late (GitHub cron drifts) does not shift the window, and a
# mid-day preview shows the last completed 8pm→8pm day rather than a partial
# one. Consecutive days abut exactly at 8pm — no gap, no overlap.
REPORT_HOUR_IST = 20


def window_bounds(now: datetime) -> tuple[datetime, datetime]:
    """(start, end) for the 8pm→8pm IST day that `now` falls in or just after.

    end   = the most recent 20:00 IST at or before `now`.
    start = end − 24h (the previous 20:00 IST).

    Both are returned as UTC-aware datetimes for the DB comparison. At a real
    8pm-IST run, end is today 20:00 IST and start is yesterday 20:00 IST."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    now_ist = now.astimezone(IST)
    end_ist = now_ist.replace(hour=REPORT_HOUR_IST, minute=0, second=0, microsecond=0)
    if now_ist < end_ist:            # before today's 8pm -> use yesterday's
        end_ist -= timedelta(days=1)
    start_ist = end_ist - WINDOW
    return start_ist.astimezone(timezone.utc), end_ist.astimezone(timezone.utc)

# How many L1/L2 category rows the ping lists. A daily volume rarely has more
# than a handful of live categories; the long tail is noise on a skim.
TOP_CATEGORIES = 6


@dataclass
class Row:
    """One review, reduced to just what the digest counts. Kept tiny and plain
    so tests build cohorts by hand without touching the ORM."""
    solved: bool = False           # reached 'sent' — reply-sent OR rca-posted
    picked_up_by: str = ""
    tier: int | None = None        # match_tier: 1, 2, or None (-> Untraceable)
    declared_untraceable: bool = False  # closed/marked untraceable by a person
    l1: str = ""
    l2: str = ""


@dataclass
class Summary:
    received: int = 0
    solved: int = 0
    tier: dict = field(default_factory=dict)          # label -> count
    people: list = field(default_factory=list)        # (name, count) desc
    categories: list = field(default_factory=list)     # ("L1 / L2", count) desc


def _tier_label(row: Row) -> str:
    """Tier 1 / Tier 2 / Untraceable — the only three buckets tracked.

    A review DECLARED untraceable (a person closed it as "no booking found" or
    marked it untraceable off the shortlist) is Untraceable even if it still
    carries a tentative match_tier — the human's decision overrides a stale
    tentative match. Otherwise match_tier 1/2 -> Tier 1/Tier 2, and anything
    with no confirmed tier is Untraceable. No "in progress" bucket — that is not
    a tracked state, and for a completed 8pm→8pm day everything has resolved to
    one of these three. Rows always sum back to Received."""
    if row.declared_untraceable:
        return "Untraceable"
    if row.tier == 1:
        return "Tier 1"
    if row.tier == 2:
        return "Tier 2"
    return "Untraceable"


def summarize(solved_rows: list[Row], received_count: int = 0,
              solved_by_rows: list[Row] | None = None) -> Summary:
    """Two cohorts, deliberately different and separately labelled in the ping:

    * `solved_rows` — reviews SOLVED inside the fixed 8pm→8pm window. This drives
      the headline Solved count, the tier mix and the categories, so those three
      stay consistent with each other and with Solved. `received_count` (reviews
      that ARRIVED in the same window) is shown beside it for context.

    * `solved_by_rows` — reviews each person FINISHED during the report's IST
      calendar day, INCLUDING older reviews that arrived days ago. This is the
      cohort the per-person "Solved by" credit describes: an associate is
      credited for everything they cleared today, not only what fell inside the
      8pm→8pm window. When None (older callers/tests) the window rows are reused,
      so "Solved by" collapses back to the window cohort."""
    people_rows = solved_rows if solved_by_rows is None else solved_by_rows
    s = Summary(received=received_count, solved=len(solved_rows))
    tier_counts: dict[str, int] = {}
    people_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for r in solved_rows:
        tier_counts[_tier_label(r)] = tier_counts.get(_tier_label(r), 0) + 1
        l1 = (r.l1 or "").strip()
        l2 = (r.l2 or "").strip()
        if l1 or l2:
            key = " / ".join([p for p in (l1, l2) if p])
            cat_counts[key] = cat_counts.get(key, 0) + 1
    for r in people_rows:
        who = (r.picked_up_by or "").strip() or "Unassigned"
        people_counts[who] = people_counts.get(who, 0) + 1
    s.tier = tier_counts
    # People: biggest first, Unassigned always last so a real owner never hides
    # under it. Ties broken by name so the order is stable across runs.
    s.people = sorted(
        people_counts.items(),
        key=lambda kv: (kv[0] == "Unassigned", -kv[1], kv[0]))
    s.categories = sorted(
        cat_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_CATEGORIES]
    return s


# The three fixed tier buckets, ALWAYS shown (even at 0, so none is ever
# mysteriously absent), each with a colour dot so the tier mix reads at a
# glance: green matched-cleanly, amber matched-shortlist, red not-found.
_TIER_FIXED = ["Tier 1", "Tier 2", "Untraceable"]
_TIER_DOT = {"Tier 1": "🟢", "Tier 2": "🟡", "Untraceable": "🔴"}

# A visual section break. Slack has no plain-text horizontal rule, so a run of
# these box-drawing chars stands in — a clear breaker between sections without
# needing Block Kit (which would break the dashboard's editable-text preview).
_DIV = "━" * 22


def _section(title: str, rows: list[str]) -> list[str]:
    """A divider, an emoji-led *Title*, then the pre-formatted rows. Empty rows
    render nothing (the caller drops the section)."""
    if not rows:
        return []
    return [_DIV, title, *rows]


def render_digest(summary: Summary, date_label: str, window_label: str = "",
                  solved_by_label: str = "") -> str:
    s = summary
    # Two independent counts, NO ratio between them. "Received" is reviews that
    # ARRIVED in the window; "Solved" is reviews FINISHED in the window, which
    # includes backlog that arrived earlier — so Solved can exceed Received on a
    # catch-up day. Dividing one by the other produced a nonsense "130%"; the
    # honest presentation is two labelled day-counts.
    #
    # `window_label` is stamped directly under the Received/Solved numbers so the
    # team reads it as the frame THOSE two numbers cover — a fixed 8pm→8pm IST
    # day, whatever time the report is actually delivered. `solved_by_label`
    # captions the Solved-by section, because that section counts a DIFFERENT
    # (wider) frame — everything each person closed across the calendar day — so
    # its total can legitimately differ from the headline Solved, and the caption
    # is what stops that difference from reading as a bug.
    out = [f"📊  *ORM Daily — {date_label}*"]
    out.append(f"Received: *{s.received}*   ·   Solved: *{s.solved}*")
    if window_label:
        out.append(f"_{window_label}_")

    # Tier — always the three buckets, each with its colour dot.
    tier_rows = [f"{_TIER_DOT.get(lbl, '•')} {lbl} — {s.tier.get(lbl, 0)}"
                 for lbl in _TIER_FIXED]
    out += _section("*🏷️  Reviews by tier*", tier_rows)

    if s.people:
        title = "*🧑‍💻  Solved by*"
        if solved_by_label:
            title += f"  _{solved_by_label}_"
        out += _section(title, [f"• {k} — {v}" for k, v in s.people])

    if s.categories:
        out += _section("*📂  Top issue categories (L1 / L2)*",
                        [f"• {k} — {v}" for k, v in s.categories])

    return "\n".join(out)


# ── DB-facing glue ──────────────────────────────────────────────────────────

def _db_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The window as NAIVE UTC datetimes, for comparing against the DB's naive
    columns. received_at / sent_at / closed_at are all stored naive (utcnow()
    and utcfromtimestamp(...).replace(tzinfo=None)); window_bounds returns
    tz-aware UTC. Comparing a naive `timestamp` column against a tz-aware
    parameter makes Postgres reinterpret the column through the session
    timezone, which shifts the window by the offset and silently drops reviews
    solved near the 8pm edge — the exact "some people are missing from Solved
    by" bug. Stripping tzinfo makes both sides plain UTC and the comparison
    exact and timezone-independent."""
    start, end = window_bounds(now)
    return start.replace(tzinfo=None), end.replace(tzinfo=None)


def _today_bounds(now: datetime) -> tuple[datetime, datetime]:
    """The report's IST CALENDAR DAY, as naive UTC — the frame for the per-person
    "Solved by" credit. Where `_db_bounds` is the fixed 8pm→8pm window, this is
    midnight-to-midnight of the day the report is FOR (the date the 8pm cutoff
    falls on), so each associate is credited for everything they closed across
    their whole working day, not only the slice inside the 8pm→8pm window.

    The end is capped at `now`, so a live 8pm run (or a delayed 11pm one) counts
    only up to the moment it fires — never into the future — while a mid-day
    preview of a completed past day still spans that whole day. Naive UTC to
    match the DB columns, for the same reason `_db_bounds` strips tzinfo."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    _, end = window_bounds(now)                     # tz-aware UTC 8pm cutoff
    end_ist = end.astimezone(IST)
    day_start_ist = end_ist.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end_ist = day_start_ist + timedelta(days=1)
    start_utc = day_start_ist.astimezone(timezone.utc)
    end_utc = min(day_end_ist.astimezone(timezone.utc), now.astimezone(timezone.utc))
    return start_utc.replace(tzinfo=None), end_utc.replace(tzinfo=None)


def _row_from(review, draft) -> Row:
    """One review+draft reduced to a Row. `solved` here means the review's
    status is 'sent'; the caller decides which window a row belongs to."""
    # DECLARED untraceable: closed with the untraceable reason, or the associate
    # marked it untraceable off the shortlist (which sets this match_method).
    # Either is a person saying "no booking" — Untraceable even if a stale
    # tentative match_tier lingers on the draft.
    cr = (getattr(review, "close_reason", "") or "").strip().lower()
    declared = (cr.startswith("untraceable")
                or (draft is not None
                    and (draft.match_method or "") == "Marked untraceable by associate"))
    return Row(
        solved=(review.status == SENT),
        picked_up_by=review.picked_up_by or "",
        tier=(draft.match_tier if draft else None),
        declared_untraceable=declared,
        l1=((draft.l1 if draft else "") or ""),
        l2=((draft.l2 if draft else "") or ""),
    )


def received_count(db, now: datetime) -> int:
    """How many reviews ARRIVED in the 8pm→8pm window — a context number only.
    Window is [start, end): start inclusive, end exclusive."""
    from server.db import Review
    start, end = _db_bounds(now)
    return (db.query(Review)
              .filter(Review.received_at.isnot(None))
              .filter(Review.received_at >= start)
              .filter(Review.received_at < end)
              .count())


def _collect_solved_between(db, start: datetime, end: datetime) -> list[Row]:
    """Reviews FINISHED in [start, end) — by when they were finished, not when
    they arrived. A review is finished when its reply/RCA was sent (draft.sent_at)
    or it was closed out (review.closed_at); either timestamp in the window puts
    it in the cohort, regardless of received date. Shared by both frames so the
    "solved" definition is identical — only the [start, end) differs."""
    from server.db import Review, RcaDraft
    from sqlalchemy import or_, and_
    pairs = (db.query(Review, RcaDraft)
               .outerjoin(RcaDraft, RcaDraft.review_id == Review.id)
               .filter(Review.status == SENT)
               .filter(or_(
                   and_(RcaDraft.sent_at >= start, RcaDraft.sent_at < end),
                   and_(Review.closed_at >= start, Review.closed_at < end)))
               .all())
    return [_row_from(r, d) for r, d in pairs]


def collect_solved_rows(db, now: datetime) -> list[Row]:
    """The 8pm→8pm SOLVED cohort — drives the headline Solved count, the tier mix
    and the categories."""
    start, end = _db_bounds(now)
    return _collect_solved_between(db, start, end)


def collect_solved_today_rows(db, now: datetime) -> list[Row]:
    """The CALENDAR-DAY solved cohort — everything finished across the report's
    IST day, drives the per-person "Solved by" credit. Wider than the 8pm→8pm
    window on purpose: an associate is credited for every review they cleared
    today, including older ones that arrived on an earlier day."""
    start, end = _today_bounds(now)
    return _collect_solved_between(db, start, end)


def build_daily_digest(db, now: datetime | None = None) -> str:
    """The full digest text for a run at `now` (defaults to real now)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    summary = summarize(
        collect_solved_rows(db, now),
        received_count(db, now),
        solved_by_rows=collect_solved_today_rows(db, now))
    # Label with the window's END day (the 8pm cutoff date it covers up to), not
    # the raw clock — at the 8pm run these coincide, but a mid-day preview of the
    # last completed day must be dated that day, not today. %-d (no leading
    # zero) is not portable to Windows, so strip the zero by hand: "7 Sep 2026".
    start, end = window_bounds(now)
    start_ist, end_ist = start.astimezone(IST), end.astimezone(IST)
    date_label = end_ist.strftime("%d %b %Y").lstrip("0")
    day_label = end_ist.strftime("%d %b").lstrip("0")     # e.g. "13 Sep"
    # Received + Solved cover the fixed 8pm→8pm window; the caption says so.
    _d = lambda t: t.strftime("%d %b").lstrip("0")
    window_label = f"Received & solved · 8pm {_d(start_ist)} → 8pm {_d(end_ist)} IST"
    # Solved by covers the whole IST day (older reviews included) — a different,
    # wider frame, so its total can differ from Solved above. The caption is what
    # makes that difference legible instead of looking like a miscount.
    solved_by_label = f"closed on {day_label}, incl. older reviews"
    return render_digest(summary, date_label, window_label, solved_by_label)
