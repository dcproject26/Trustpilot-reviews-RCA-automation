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


def summarize(rows: list[Row]) -> Summary:
    s = Summary(received=len(rows))
    tier_counts: dict[str, int] = {}
    people_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for r in rows:
        tier_counts[_tier_label(r)] = tier_counts.get(_tier_label(r), 0) + 1
        # PEOPLE ARE COUNTED OVER SOLVED REVIEWS ONLY — this is "who cleared the
        # work", so it sums to Solved and never lists "Unassigned" as if an
        # untouched review had been picked up. Counting over every received
        # review put "Unassigned N" under a heading that says the opposite, and
        # made the owner totals exceed the solved count — the exact confusion
        # flagged on the first version.
        if r.solved:
            s.solved += 1
            who = (r.picked_up_by or "").strip() or "Unassigned"
            people_counts[who] = people_counts.get(who, 0) + 1
        l1 = (r.l1 or "").strip()
        l2 = (r.l2 or "").strip()
        if l1 or l2:
            key = " / ".join([p for p in (l1, l2) if p])
            cat_counts[key] = cat_counts.get(key, 0) + 1
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


def render_digest(summary: Summary, date_label: str) -> str:
    s = summary
    rate = round(s.solved / s.received * 100) if s.received else 0
    out = [f"📊  *ORM Daily — {date_label}*",
           f"Reviews received: *{s.received}*   ·   Solved: *{s.solved}* ({rate}%)"]

    # Tier — always the three buckets, each with its colour dot.
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


# ── DB-facing glue ──────────────────────────────────────────────────────────

def collect_rows(db, now: datetime) -> list[Row]:
    """Reviews received in the 8pm→8pm IST day containing `now`, reduced to Row.

    The window is [start, end): start inclusive, end exclusive — a review at
    exactly 8pm belongs to the NEXT day's report, never both, so consecutive
    days never double-count it."""
    from server.db import Review
    start, end = window_bounds(now)
    reviews = (db.query(Review)
                 .filter(Review.received_at.isnot(None))
                 .filter(Review.received_at >= start)
                 .filter(Review.received_at < end)
                 .all())
    rows = []
    for r in reviews:
        d = r.draft
        # DECLARED untraceable: closed with the untraceable reason, or the
        # associate marked it untraceable off the shortlist (which sets this
        # match_method). Either is a person saying "no booking" — it counts as
        # Untraceable even if a stale tentative match_tier lingers on the draft.
        cr = (r.close_reason or "").strip().lower()
        declared = (cr.startswith("untraceable")
                    or (d is not None
                        and (d.match_method or "") == "Marked untraceable by associate"))
        rows.append(Row(
            solved=(r.status == SENT),
            picked_up_by=r.picked_up_by or "",
            tier=(d.match_tier if d else None),
            declared_untraceable=declared,
            l1=((d.l1 if d else "") or ""),
            l2=((d.l2 if d else "") or ""),
        ))
    return rows


def build_daily_digest(db, now: datetime | None = None) -> str:
    """The full digest text for a run at `now` (defaults to real now)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    rows = collect_rows(db, now)
    # Label with the window's END day (the 8pm cutoff date it covers up to), not
    # the raw clock — at the 8pm run these coincide, but a mid-day preview of the
    # last completed day must be dated that day, not today. %-d (no leading
    # zero) is not portable to Windows, so strip the zero by hand: "7 Sep 2026".
    _, end = window_bounds(now)
    date_label = end.astimezone(IST).strftime("%d %b %Y").lstrip("0")
    return render_digest(summarize(rows), date_label)
