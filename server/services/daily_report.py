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

from server.tiers import classify, UNTRACEABLE, SENT

IST = timezone(timedelta(hours=5, minutes=30))
WINDOW = timedelta(hours=24)

# How many L1/L2 category rows the ping lists. A daily volume rarely has more
# than a handful of live categories; the long tail is noise on a skim.
TOP_CATEGORIES = 6


@dataclass
class Row:
    """One review, reduced to just what the digest counts. Kept tiny and plain
    so tests build cohorts by hand without touching the ORM."""
    status: str = ""
    solved: bool = False           # reached 'sent' — reply-sent OR rca-posted
    picked_up_by: str = ""
    bucket: str = ""               # from tiers.classify()
    tier: int | None = None        # match_tier: 1, 2, or None
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
    """T1 / T2 / Untraceable / Pending for one review.

    Untraceable is the AUTHORITATIVE bucket from tiers.classify() — a review the
    search ran on and found nothing for — never merely 'tier is None' (a review
    still processing also has no tier, and is not untraceable). T1/T2 come from
    match_tier. Anything else — still processing, or a shortlist awaiting a
    human — is Pending, so the tier rows always sum back to Received rather than
    quietly dropping the in-flight ones (CLAUDE.md rule 1)."""
    if row.bucket == UNTRACEABLE:
        return "Untraceable"
    if row.tier == 1:
        return "T1"
    if row.tier == 2:
        return "T2"
    return "Pending"


def summarize(rows: list[Row]) -> Summary:
    s = Summary(received=len(rows))
    tier_counts: dict[str, int] = {}
    people_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    for r in rows:
        if r.solved:
            s.solved += 1
        tier_counts[_tier_label(r)] = tier_counts.get(_tier_label(r), 0) + 1
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


# Fixed order for the tier rows; Pending is shown only when it is non-zero.
_TIER_ORDER = ["T1", "T2", "Untraceable", "Pending"]


def _block(title: str, pairs: list[tuple[str, int]]) -> list[str]:
    """A titled block: *Title* then aligned '• name   count' rows. Empty pairs
    render nothing (the caller drops the block)."""
    if not pairs:
        return []
    width = max(len(str(k)) for k, _ in pairs)
    lines = [f"*{title}*"]
    for k, v in pairs:
        lines.append(f"• {str(k).ljust(width)}   {v}")
    return lines


def render_digest(summary: Summary, date_label: str) -> str:
    s = summary
    rate = round(s.solved / s.received * 100) if s.received else 0
    out = [f"*ORM Daily — {date_label}*",
           "",
           f"*{s.received} in*  ·  *{s.solved} solved*  ·  *{rate}%*"]

    tier_pairs = [(lbl, s.tier[lbl]) for lbl in _TIER_ORDER
                  if s.tier.get(lbl)]                # Pending only when > 0
    if tier_pairs:
        out += [""] + _block("Tier", tier_pairs)

    if s.people:
        out += [""] + _block("Picked up by", s.people)

    if s.categories:
        out += [""] + _block("Top categories", s.categories)

    return "\n".join(out)


# ── DB-facing glue ──────────────────────────────────────────────────────────

def collect_rows(db, now: datetime) -> list[Row]:
    """Reviews received in the last 24h, reduced to Row. now MUST be tz-aware."""
    from server.db import Review
    start = now - WINDOW
    reviews = (db.query(Review)
                 .filter(Review.received_at.isnot(None))
                 .filter(Review.received_at >= start)
                 .filter(Review.received_at <= now)
                 .all())
    rows = []
    for r in reviews:
        d = r.draft
        rows.append(Row(
            status=r.status or "",
            solved=(r.status == SENT),
            picked_up_by=r.picked_up_by or "",
            bucket=classify(r, d),
            tier=(d.match_tier if d else None),
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
    # %-d (no leading zero) is not portable to Windows, so strip the zero by
    # hand — the label reads "7 Sep 2026", never "07 Sep 2026", on any platform.
    date_label = now.astimezone(IST).strftime("%d %b %Y").lstrip("0")
    return render_digest(summarize(rows), date_label)
