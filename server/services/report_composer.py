"""Composes the Slack report the Reporting page sends — daily, weekly or a custom
range — from the SAME query engine the Explore tab reads.

Why it shares `reporting_query` rather than counting for itself: the dashboard and
the message a manager receives must never disagree. A second set of counters is a
second definition of "solved", and the two drift the first time one is edited.

The composed text is a DRAFT. The dashboard shows it in an editable box and posts
exactly what the person leaves there — this module never posts.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from server.services.reporting_query import (
    DIM_BY_KEY, MEASURES, run_query)

DIVIDER = "━" * 22

# Sections a report can carry, beyond the headline: any dimension is allowed, so
# the builder is not a fixed list that always misses the cut someone needs.
DEFAULT_SECTIONS = ["tier", "owner", "l1"]


def _fmt(measure_key: str, value) -> str:
    """One measure value as it should read in Slack. `None` is "—", never 0 —
    "no review was timed" and "it took zero hours" are different facts."""
    if value is None:
        return "—"
    if measure_key.endswith("_pct"):
        return f"{value:.0f}%"
    if measure_key in ("avg_rating", "avg_issues"):
        return f"{value:.2f}"
    if measure_key == "median_tts":
        return f"{value:.1f}h"
    return str(int(value))


def _d(day: str) -> str:
    """'2026-09-13' -> '13 Sep'. %-d is not portable to Windows, so the leading
    zero is stripped by hand."""
    return datetime.strptime(day, "%Y-%m-%d").strftime("%d %b").lstrip("0")


def window_for(preset: str, date_from: str | None, date_to: str | None,
               today: str | None = None) -> tuple[str, str]:
    """(from, to) inclusive dates for a preset. 'custom' uses what it was given."""
    end = date_to or today or datetime.utcnow().strftime("%Y-%m-%d")
    if preset == "daily":
        return end, end
    if preset == "weekly":
        start = (datetime.strptime(end, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d")
        return start, end
    if not date_from:
        raise ValueError("a custom report needs date_from; try preset='weekly' instead")
    return date_from, end


def compose(records: list[dict], preset: str, date_from: str, date_to: str,
            sections: list[str] | None = None, rank_by: str = "count",
            top: int = 5, filters: dict | None = None) -> str:
    """The Slack text for this window. Every number comes from `run_query`, so the
    draft and the Explore tab cannot disagree."""
    if rank_by not in MEASURES:
        raise ValueError(f"unknown measure: {rank_by}")
    sections = [s for s in (sections or DEFAULT_SECTIONS)]
    bad = [s for s in sections if s not in DIM_BY_KEY]
    if bad:
        raise ValueError("unknown section(s): " + ", ".join(bad))

    title = {"daily": "ORM Daily", "weekly": "ORM Weekly"}.get(preset, "ORM Report")
    head = run_query(records, [], ["count", "solved", "posted"], filters=filters)
    received = head["totals"]["count"] or 0

    out = [f"📊  *{title}*",
           f"Received: *{received}*   ·   Solved: *{head['totals']['solved'] or 0}*"
           f"   ·   RCAs posted: *{head['totals']['posted'] or 0}*"]
    out.append(f"_{_d(date_from)} → {_d(date_to)} IST_" if date_from != date_to
               else f"_{_d(date_to)} IST_")
    if filters:
        applied = " · ".join(f"{DIM_BY_KEY[k].label} = {v}"
                             for k, v in filters.items() if v and k in DIM_BY_KEY)
        if applied:
            # The reader must know the numbers are a slice, not the whole day.
            out.append(f"_Filtered to: {applied}_")

    if not received:
        # An empty window is a fact worth stating plainly, not an empty message.
        out.append(DIVIDER)
        out.append("No reviews arrived in this window.")
        return "\n".join(out)

    for key in sections:
        res = run_query(records, [key], [rank_by], filters=filters,
                        limit=top, sort=rank_by)
        if not res["rows"]:
            continue
        label = DIM_BY_KEY[key].label
        out.append(DIVIDER)
        out.append(f"*{label}*  _by {MEASURES[rank_by][0].lower()}_")
        for row in res["rows"]:
            out.append(f"• {row['key'][0]} — {_fmt(rank_by, row['values'][rank_by])}")
        # Rule 1: a short list because nobody recorded the field is NOT the same
        # as a short list because the work did not happen.
        missing = res["unset"].get(key, 0)
        if missing:
            out.append(f"_{missing} review(s) have no {label.lower()} — not counted above._")
        if res["truncated"]:
            out.append(f"_Top {top} of {res['row_count']}._")

    med = run_query(records, [], ["median_tts"], filters=filters)["totals"]["median_tts"]
    out.append(DIVIDER)
    out.append(f"*Median time to send* — {_fmt('median_tts', med)}")
    return "\n".join(out)


def build(db, preset: str = "daily", date_from: str | None = None,
          date_to: str | None = None, sections: list[str] | None = None,
          rank_by: str = "count", top: int = 5,
          filters: dict | None = None, today: str | None = None) -> dict:
    """DB-facing: resolve the window, read the records, return the draft text."""
    from server.services.reporting_query import records as load
    start, end = window_for(preset, date_from, date_to, today)
    lo = datetime.strptime(start, "%Y-%m-%d")
    hi = datetime.strptime(end, "%Y-%m-%d") + timedelta(days=1)   # end is inclusive
    recs = load(db, lo, hi)
    return {"text": compose(recs, preset, start, end, sections, rank_by, top, filters),
            "date_from": start, "date_to": end, "reviews": len(recs)}
