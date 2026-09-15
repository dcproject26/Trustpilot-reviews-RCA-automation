"""The daily ORM digest posted to the reports channel at 8pm IST.

READ-ONLY, and cheap: a few group-by passes over rows already in Postgres
(`reviews` + their `rca_drafts`). It runs NO BigQuery lookup and NO model call —
those happen only inside a review's own pipeline run; this just reads what those
runs already wrote.

TWO KINDS OF NUMBER, and the report keeps them apart on purpose:

  * a STOCK — "total pending reviews", the open backlog as it stands right now,
    all-time and unwindowed, plus the tier mix and the top categories WITHIN that
    backlog. These answer "how much is outstanding, and what is it".
  * a FLOW — "received today" and "solved today, by whom", inside a fixed
    8pm→8pm IST day. These answer "what moved in the last 24 hours".

The 24h frame is anchored to 20:00 IST rather than rolling from the run time, so
a scheduler firing late does not shift it, and consecutive days abut exactly at
8pm — no gap, no overlap, every review counted once. The per-person "Solved by"
credit uses that SAME window; it used to use the IST calendar day capped at the
run time, which dropped every solve between 8pm and midnight into a hole between
today's cap and tomorrow's start — 57 of 185 solves on the real export.

NO percentage crosses two cohorts. Solved-over-Received is the forbidden one:
Solved includes backlog that arrived days earlier, and dividing the two once
shipped a "130%" here. Each share printed divides a cohort by itself.

Three layers, so the arithmetic is testable without a database:
  * `summarize(rows)`      — pure: counts -> a Summary.
  * `render_digest(...)`   — pure: a Summary -> the Slack text.
  * `collect_*(db, ...)`/`build_daily_digest(db, now)` — the DB-facing glue.
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
    pending: int | None = None      # open backlog, all-time; None = not asked for
    tier: dict = field(default_factory=dict)          # label -> count
    people: list = field(default_factory=list)        # (name, count) desc
    categories: list = field(default_factory=list)     # ("L1 / L2", count) desc
    # The cohort the tier mix and the categories were counted over, and how many
    # of its rows carried NO L1/L2 at all. `mix_base` is the ONLY honest
    # denominator for the tier/category percentages: both are counted over the
    # same rows, so each share is "x% of this cohort" and never a cross-cohort
    # ratio. `uncategorised` exists because a category block that silently omits
    # rows with no L1/L2 looks identical to one where every row had a category —
    # the ran-and-found-nothing rule. It is reported, not dropped.
    mix_base: int = 0
    uncategorised: int = 0


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


def _norm_owner(picked_up_by: str) -> str:
    """The associate credited for a solve, or "Unassigned".

    A blank owner and a test account are both "nobody real": crediting "Test"
    in a report a manager reads is noise, and it once sat second in the
    time-to-send ranking. One definition, shared with the Reporting page."""
    from server.services.reporting_query import TEST_OWNERS, UNASSIGNED
    name = (picked_up_by or "").strip()
    return UNASSIGNED if (not name or name.lower() in TEST_OWNERS) else name


def summarize(solved_rows: list[Row], received_count: int = 0,
              pending_rows: list[Row] | None = None,
              pending_count: int | None = None,
              mix_rows: list[Row] | None = None) -> Summary:
    """Counts, from at most two cohorts that are never mixed into one ratio:

    * `solved_rows` — reviews SOLVED inside the fixed 8pm→8pm window. Drives the
      headline Solved count AND the per-person "Solved by" credit. These are the
      SAME cohort on purpose: consecutive 8pm→8pm windows tile with no gap and no
      overlap, so every solve is credited to exactly one person on exactly one
      day. The previous per-person frame was the IST calendar day capped at the
      run time, which left work finished between 8pm and midnight after today's
      cap and before tomorrow's start — credited to nobody. On the real export
      that swallowed 57 of 185 solves (31%), 25 of them one associate's.
      `received_count` (reviews that ARRIVED in the same window) sits beside
      Solved as a second labelled count — never as a denominator for it.

    * `pending_rows` — the OPEN BACKLOG as it stands now (all-time, not
      windowed). When given, the tier mix and the categories are counted over
      THIS cohort, because "how much is outstanding, and of what" is a question
      about the backlog rather than about one day's finished work. When None the
      tier mix and categories fall back to `solved_rows`, which is what the
      weekly digest wants (its blocks describe the week it summarises)."""
    # THREE cohorts, kept apart on purpose:
    #   * solved_rows    — finished inside the 8pm→8pm window. Drives Solved and
    #                      the per-person credit.
    #   * pending_count  — the open backlog as a STOCK. A number only: it is the
    #                      headline, and nothing is divided by it.
    #   * mix_rows       — what the tier mix is counted over. For the daily this
    #                      is the 24h cohort (everything received OR solved in the
    #                      window), because "what did we handle today, and what
    #                      shape was it" is a question about the day's work —
    #                      solved and still-open alike. Falling back to solved_rows
    #                      keeps the weekly digest, which passes neither, correct.
    mix = (mix_rows if mix_rows is not None
           else (pending_rows if pending_rows is not None else solved_rows))
    pending_n = (pending_count if pending_count is not None
                 else (None if pending_rows is None else len(pending_rows)))
    s = Summary(received=received_count, solved=len(solved_rows),
                pending=pending_n, mix_base=len(mix))
    tier_counts: dict[str, int] = {}
    people_counts: dict[str, int] = {}
    cat_counts: dict[str, int] = {}
    uncategorised = 0
    for r in mix:
        tier_counts[_tier_label(r)] = tier_counts.get(_tier_label(r), 0) + 1
        l1 = (r.l1 or "").strip()
        l2 = (r.l2 or "").strip()
        if l1 or l2:
            key = " / ".join([p for p in (l1, l2) if p])
            cat_counts[key] = cat_counts.get(key, 0) + 1
        else:
            # Counted, not skipped: see Summary.uncategorised.
            uncategorised += 1
    for r in solved_rows:
        # Test accounts are not people whose workload belongs in a report. The
        # REVIEW still counts everywhere else — only the attribution is dropped.
        # TEST_OWNERS is imported rather than re-listed so the daily digest and
        # the Reporting page cannot disagree about who is a real associate.
        who = _norm_owner(r.picked_up_by)
        people_counts[who] = people_counts.get(who, 0) + 1
    s.tier = tier_counts
    s.uncategorised = uncategorised
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


def _pct(n: int, base: int) -> str:
    """" (39%)" — or "" when there is no denominator to divide by.

    A percentage of nothing is not 0%, it is unanswerable, so an empty cohort
    prints the count alone rather than a fabricated "0%". Rounded to whole
    points; rounded shares need not sum to exactly 100 and nothing claims they
    do."""
    if base <= 0:
        return ""
    return f" ({round(100 * n / base)}%)"


def render_digest(summary: Summary, date_label: str, window_label: str = "",
                  pending_label: str = "") -> str:
    """The digest, in the order the numbers are actually read:

      1. Total pending — the open backlog RIGHT NOW, all-time. The headline,
         because it is the only number that says how deep the hole is.
      2/3. Received and Solved — both inside the fixed 8pm→8pm IST day, stamped
         with that window so nobody reads them as "since midnight".
      4. Solved by — the same 24h cohort as Solved, so the per-person rows sum
         back to Solved exactly and each row can carry its share of it.
      5. Tier mix and top categories — counted over the PENDING backlog, not
         over the day's solved work, because these answer "what is stuck".

    PERCENTAGES, and the ones deliberately absent. Every percentage here divides
    a cohort by ITSELF: a person's solves by the window's Solved total; a tier or
    a category by the pending total those rows came from. There is NO
    Solved-over-Received figure — they are different cohorts (Solved includes
    backlog that arrived days earlier), which is exactly how this report once
    shipped a "130%". Two labelled counts is the honest form, and `_pct` refuses
    to divide by an empty cohort at all."""
    s = summary
    out = [f"📊  *ORM Daily — {date_label}*"]

    # 1. The backlog headline. Rendered only when a pending cohort was actually
    # supplied: `pending=None` means nobody asked for it, which is not the same
    # as a backlog of zero, and printing "Pending: 0" for it would be a lie.
    if s.pending is not None:
        out.append(f"🗂️  Total pending reviews: *{s.pending}*")
        if pending_label:
            out.append(pending_label)

    # 2/3. The 24h frame.
    #
    # Solved carries a share, and the denominator is the OPEN WORKLOAD it came
    # out of: what is still pending plus what was cleared in the window. That is
    # the pile as it stood before today's clearing, so the figure answers "how
    # much of the backlog did we take down" and can never exceed 100%.
    #
    # It is deliberately NOT Solved-over-Received. Those are different cohorts —
    # Solved includes backlog that arrived days earlier — and on 11 Sep the real
    # export gives Received 7 against Solved 42, which is exactly how this report
    # once shipped a "130%". Received therefore carries no share: there is no
    # honest denominator for "what arrived".
    solved_row = f"• Solved today — *{s.solved}*"
    if s.pending is not None:
        open_pile = s.pending + s.solved
        if open_pile > 0:
            # The denominator is SPELLED OUT. A bare "of 106" sends the reader
            # hunting for where 106 came from; showing the addition means the
            # figure can be checked against the two numbers already on screen.
            solved_row += (f" ({round(s.solved / open_pile * 100)}% of {open_pile}"
                           f" = {s.pending} pending + {s.solved} solved)")
    day_rows = [f"• Received today — *{s.received}*", solved_row]
    title = "*📬  Last 24 hours*"
    if window_label:
        title += f"  ({window_label})"
    out += _section(title, day_rows)

    # 4. Solved by — the same 24h cohort as Solved, so these rows still sum back
    # to it exactly. Counts only: the per-person share was asked to be dropped,
    # and the count is the thing a manager acts on.
    if s.people:
        out += _section("*🧑‍💻  Solved by*",
                        [f"• {k} — {v}" for k, v in s.people])

    # 5. Tier — always the three buckets, each with its colour dot, as a share of
    # the cohort they were counted over (the pending backlog for the daily).
    # The block is named after the cohort it actually counted. With no pending
    # cohort supplied the mix falls back to the solved rows, and calling that
    # "Pending by tier" would mislabel the number rather than just omit it.
    over_pending = s.pending is not None
    # The "% of N pending" caption is only printed when there is something to
    # divide by. On an empty backlog `_pct` prints no shares at all, so the
    # caption would be promising percentages that no row carries — and "% of 0"
    # is not a denominator.
    base_note = (f"  (% of {s.mix_base} handled in 24h)"
                 if over_pending and s.mix_base > 0 else "")
    tier_title = "Tier — last 24 hours" if over_pending else "Reviews by tier"
    # The share is printed ONLY when the caption states what it is a share OF.
    # A bare "(33%)" with no denominator on screen cannot be checked, and an
    # unverifiable number is worse than no number.
    tier_rows = [f"{_TIER_DOT.get(lbl, '•')} {lbl} — {s.tier.get(lbl, 0)}"
                 f"{_pct(s.tier.get(lbl, 0), s.mix_base) if base_note else ''}"
                 for lbl in _TIER_FIXED]
    out += _section(f"*🏷️  {tier_title}*{base_note}", tier_rows)

    # NO category block. The digest is a glance at how deep the backlog is and
    # who is clearing it; the category breakdown lives in the Reporting page,
    # where it can be sliced instead of truncated to six rows. `summarize` still
    # counts categories because the weekly digest renders them.

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


def _received_between(db, start: datetime, end: datetime) -> int:
    """How many reviews ARRIVED in [start, end): start inclusive, end exclusive.
    Shared by the daily 8pm→8pm window and the weekly window so "received" means
    the same thing at both scales — and so 7 daily windows sum to one week."""
    from server.db import Review
    return (db.query(Review)
              .filter(Review.received_at.isnot(None))
              .filter(Review.received_at >= start)
              .filter(Review.received_at < end)
              .count())


def received_count(db, now: datetime) -> int:
    """How many reviews ARRIVED in the 8pm→8pm window — a context number only."""
    start, end = _db_bounds(now)
    return _received_between(db, start, end)


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


def collect_pending_rows(db) -> list[Row]:
    """The OPEN BACKLOG: every review that has not reached 'sent', all-time.

    NOT windowed, and that is the point — "total pending" is a stock, not a flow.
    A review that arrived three weeks ago and is still open is still open today,
    so bounding this by the 24h window would report a backlog of only the last
    day's leftovers and make a growing queue look flat. `now` is not a parameter
    because the answer does not depend on it.

    `status != SENT` is the whole test, and it agrees with how the rest of the
    codebase defines done: `tiers.classify()` puts a 'sent' review in the SENT
    bucket ahead of every other rule, and a review finished WITHOUT a Slack post
    (`sent_route == 'closed'`) is still written as status 'sent' — a person
    resolved it, so it is solved, not pending. On the real export all 86
    `sent_route == 'closed'` rows carry status 'sent', and no non-'sent' row
    carries a sent_at or closed_at at all, so the status flag and the finish
    timestamps never disagree about who is pending.

    NULL status is counted as PENDING, explicitly. `status` is a nullable column,
    and in SQL `status != 'sent'` is NULL — not true — for a NULL row, so the
    plain comparison would drop such a review from the backlog silently: it would
    appear in neither Pending nor Solved and the total would just be short, with
    nothing to show it had happened. A review with no status has certainly not
    been sent, so it belongs in the backlog."""
    from server.db import Review, RcaDraft
    from sqlalchemy import or_
    pairs = (db.query(Review, RcaDraft)
               .outerjoin(RcaDraft, RcaDraft.review_id == Review.id)
               .filter(or_(Review.status.is_(None), Review.status != SENT))
               .all())
    return [_row_from(r, d) for r, d in pairs]


def pending_count(db) -> int:
    """How many reviews are open RIGHT NOW. A stock, not a flow: no window.

    Counted rather than materialised because the headline needs the number and
    nothing is divided by it. NULL status counts as pending — SQL `!= 'sent'` is
    NULL for a NULL row, which would drop such a review from both Pending and
    Solved and leave it in neither."""
    from server.db import Review
    from sqlalchemy import or_
    return (db.query(Review)
              .filter(or_(Review.status.is_(None), Review.status != SENT))
              .count())


def collect_window_rows(db, now: datetime) -> list[Row]:
    """Everything the team HANDLED in the 8pm→8pm window: reviews that arrived in
    it, plus reviews finished in it that arrived earlier. Deduplicated, because a
    review that both arrived and was solved today must be counted once.

    This is the cohort the tier mix describes — the day's work, solved and still
    open alike — rather than the all-time backlog, which answers a different
    question and moves far more slowly than a daily report should."""
    from server.db import Review, RcaDraft
    from sqlalchemy import or_, and_
    start, end = _db_bounds(now)
    pairs = (db.query(Review, RcaDraft)
               .outerjoin(RcaDraft, RcaDraft.review_id == Review.id)
               .filter(or_(
                   and_(Review.received_at >= start, Review.received_at < end),
                   and_(RcaDraft.sent_at >= start, RcaDraft.sent_at < end),
                   and_(Review.closed_at >= start, Review.closed_at < end)))
               .all())
    # No de-duplication: RcaDraft.review_id is UNIQUE, so a review joins to at
    # most one draft and matching several arms of the OR still yields one row.
    # A dedup pass here would be a guard nothing can reach — which reads as
    # protection while proving nothing.
    return [_row_from(r, d) for r, d in pairs]


def build_daily_digest(db, now: datetime | None = None) -> str:
    """The full digest text for a run at `now` (defaults to real now)."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    summary = summarize(
        collect_solved_rows(db, now),
        received_count(db, now),
        pending_count=pending_count(db),
        mix_rows=collect_window_rows(db, now))
    # Label with the window's END day (the 8pm cutoff date it covers up to), not
    # the raw clock — at the 8pm run these coincide, but a mid-day preview of the
    # last completed day must be dated that day, not today. %-d (no leading
    # zero) is not portable to Windows, so strip the zero by hand: "7 Sep 2026".
    start, end = window_bounds(now)
    start_ist, end_ist = start.astimezone(IST), end.astimezone(IST)
    date_label = end_ist.strftime("%d %b %Y").lstrip("0")
    # Received, Solved and Solved-by all cover the fixed 8pm→8pm window; the
    # caption says so once, over the block those numbers live in.
    _d = lambda t: t.strftime("%d %b").lstrip("0")
    window_label = f"8pm {_d(start_ist)} → 8pm {_d(end_ist)} IST"
    # The backlog is a stock with no window — captioned so it is never read as
    # "pending that arrived today", which would be a much smaller number.
    pending_label = "(all time)"
    return render_digest(summary, date_label, window_label, pending_label)
