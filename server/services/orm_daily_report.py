"""The ORM internal end-of-day report: who solved how many, by platform.

Reads the team's `ORM RAW` tab — every platform's cases in one sheet, Trustpilot
rows written by this tool, social-media and app rows logged by hand — and posts
a per-owner count for the day to the ORM internal channel at 8pm IST.

    Owner        Social Media   Trustpilot   App Feedback Form
    Devshree              2            8                   1
    Paul                  0            4                   0
    ...

ONE SHEET, THREE BUCKETS. Source is a free-text platform name. Trustpilot is its
own column; "App Feedback Form" is its own column; EVERYTHING ELSE (Instagram,
Facebook, Playstore, Twitter, …) is Social Media. The rule is a denylist of the
two named buckets, not an allowlist of SM platforms, so a new social channel
counts without a code change rather than silently falling out.

Pure where it can be: `count_rows()` and `render()` take plain rows and a date,
so the tallying and the table are testable without a live sheet. Only
`build_report()` reads the sheet.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, date

from server.services.daily_report import IST

log = logging.getLogger("orm_daily_report")

TRUSTPILOT = "Trustpilot"
APP_FEEDBACK = "App Feedback"
ESCALATIONS = "Escalations raised Internally"
SOCIAL_MEDIA = "Social Media"
# The report's column order. Trustpilot, app-alert feedback and internally-raised
# escalations each have their own label; Social Media is everything else.
PLATFORMS = [SOCIAL_MEDIA, TRUSTPILOT, APP_FEEDBACK, ESCALATIONS]

# Owners that are not people. Dropped from the roster, case-insensitively.
_NON_OWNERS = {"", "test", "unassigned", "(none)", "na", "n/a"}

# Canonical owner names. The team's manual entries (Social Media, Escalations)
# use full names; our Trustpilot rows sometimes carry a shortened form from
# picked_up_by. Map shortened → canonical so the report merges them.
_OWNER_ALIASES: dict[str, str] = {
    "devshree": "Devshree C",
    "swagatom": "Abo Tani",
    "samson j c": "Paul",
}


def _canon_owner(name: str) -> str:
    """The canonical display name for a sheet owner."""
    s = str(name or "").strip().lstrip("@").strip()
    return _OWNER_ALIASES.get(s.lower(), s)

# ORM RAW column indexes (0-based), from the live header on 2026-10-21:
# A Date, B Source, …, I Owner.
_COL_DATE = 0
_COL_SOURCE = 1
_COL_OWNER = 8

# The date formats seen in the sheet's Date column across platforms: this
# tool writes "October 21, 2026"; hand-logged rows use "1 April 2025",
# "April 1, 2025" or "7/7/2025". Tried in order; an unparseable cell is COUNTED
# as unparsed and reported, never silently treated as not-today.
_DATE_FORMATS = ("%B %d, %Y", "%d %B %Y", "%B %d %Y", "%m/%d/%Y", "%d/%m/%Y",
                 "%Y-%m-%d")


def platform_of(source: str) -> str:
    """Which of the three report columns this Source falls in.

    Trustpilot and app-alert feedback each have their OWN label; everything else
    — Instagram, Facebook, Twitter, LinkedIn, PlayStore, App Store, … — is
    Social Media. App feedback is logged as "APP Feedback" (and a few older rows
    as "App Feedback Form"), so the match is the "app feedback" prefix, which
    catches both and nothing else (an app STORE is "app store", not "app
    feedback"). A denylist of the two named buckets, so a new SM channel counts
    with no code change."""
    s = str(source or "").strip().lower()
    if s == "trustpilot":
        return TRUSTPILOT
    if s.startswith("app feedback"):
        return APP_FEEDBACK
    if s.startswith("escalation"):
        return ESCALATIONS
    return SOCIAL_MEDIA


def _parse_date(cell: str):
    """A date, or None when the cell is blank or in no known format."""
    s = str(cell or "").strip()
    if not s:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def report_day(now: datetime | None = None) -> date:
    """The IST calendar day the report covers — the day it runs."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    return now.astimezone(IST).date()


def count_rows(rows, day: date) -> tuple[dict, dict]:
    """(counts, meta) for one day.

    counts: {owner: {platform: n}} over rows whose Date parses to `day`.
    meta:   {"unparsed_dates": n, "skipped_non_owner": n} — what did NOT make it
            in, so an empty report is distinguishable from a read that silently
            dropped rows (CLAUDE.md rule 1)."""
    counts: dict = {}
    unparsed = 0
    skipped = 0
    for r in rows:
        if not isinstance(r, (list, tuple)):
            continue
        cell = r[_COL_DATE] if len(r) > _COL_DATE else ""
        d = _parse_date(cell)
        if d is None:
            s = str(cell or "").strip()
            # A spreadsheet error value (#REF!, #N/A, …) is junk, not a date we
            # failed to read — skip it silently rather than flag it as unreadable.
            if s and s.upper() not in _SPREADSHEET_ERRORS:
                unparsed += 1
            continue
        if d != day:
            continue
        owner = _canon_owner(r[_COL_OWNER] if len(r) > _COL_OWNER else "")
        if owner.lower() in _NON_OWNERS:
            skipped += 1
            continue
        source = r[_COL_SOURCE] if len(r) > _COL_SOURCE else ""
        plat = platform_of(source)
        counts.setdefault(owner, {p: 0 for p in PLATFORMS})[plat] += 1
    return counts, {"unparsed_dates": unparsed, "skipped_non_owner": skipped}


# Spreadsheet error values that land in the Date column (e.g. a broken formula
# reference). They are junk, not dates we failed to parse, so they must not be
# flagged as "unreadable" — that footnote should mean a real malformed date.
_SPREADSHEET_ERRORS = {"#REF!", "#N/A", "#ERROR!", "#VALUE!", "#DIV/0!",
                       "#NAME?", "#NULL!", "#NUM!", "#SPILL!", "#CALC!"}


def _fmt_day(day: date) -> str:
    # Lead with the weekday so the report reads e.g. "Thursday, October 22, 2026".
    return f"{day.strftime('%A')}, {day.strftime('%B')} {day.day}, {day.year}"


def render(counts: dict, day: date, meta: dict | None = None) -> str:
    """The Slack message: a monospace table of owners by platform.

    Owners are sorted by their day's total, descending, so the busiest reads
    first. A day with no cases says so plainly rather than posting an empty
    grid."""
    meta = meta or {}
    head = f"*ORM — cases solved {_fmt_day(day)}*"
    if not counts:
        note = ""
        if meta.get("unparsed_dates"):
            note = (f"\n_({meta['unparsed_dates']} row(s) had a date we could "
                    f"not read and were not counted.)_")
        return head + "\nNo cases logged for today." + note

    owners = sorted(counts, key=lambda o: o.lower())
    name_w = max([len("Owner")] + [len(o) for o in owners])
    headers = list(PLATFORMS)
    # Short column captions so the table fits a Slack code block on a phone.
    caps = {SOCIAL_MEDIA: "Social", TRUSTPILOT: "Trustpilot",
            APP_FEEDBACK: "App Fdbk", ESCALATIONS: "Esc(Int)"}
    widths = {p: max(len(caps[p]), 3) for p in headers}

    def line(name, cells):
        row = name.ljust(name_w)
        for p in headers:
            row += "  " + str(cells[p]).rjust(widths[p])
        return row

    header_line = line("Owner", {p: caps[p] for p in headers})
    rule = "-" * len(header_line)                      # separators span the table
    lines = [header_line, rule]
    totals = {p: 0 for p in headers}
    for o in owners:
        lines.append(line(o, {p: counts[o].get(p, 0) for p in headers}))
        for p in headers:
            totals[p] += counts[o].get(p, 0)
    lines.append(rule)                                 # a line before the total
    lines.append(line("TOTAL", totals))

    body = "```\n" + "\n".join(lines) + "\n```"
    foot = ""
    if meta.get("unparsed_dates"):
        foot = (f"\n_{meta['unparsed_dates']} row(s) had an unreadable date and "
                f"were not counted._")
    return f"{head}\n{body}{foot}"


def build_report(now: datetime | None = None, day: str | date | None = None) -> dict:
    """Read ORM RAW and build the report. Defaults to today (IST); pass `day`
    (a date or 'YYYY-MM-DD') to build a specific day's report — e.g. to re-post a
    day the 8pm run missed. Returns {text, counts, meta, error, day} — text is the
    Slack message, or an error string when the sheet could not be read."""
    from server.config import (ORM_REPORT_SHEET_ID, ORM_REPORT_SHEET_TAB,
                               ORM_MASTER_SHEET_ID)
    from server.services.sheet_export import SheetIO
    if day:
        try:
            day = date.fromisoformat(day) if isinstance(day, str) else day
        except ValueError:
            return {"text": "", "counts": {}, "meta": {}, "day": str(day),
                    "error": f"bad day {day!r}: expected YYYY-MM-DD"}
    else:
        day = report_day(now)
    out = {"text": "", "counts": {}, "meta": {}, "error": "", "day": str(day)}
    if not ORM_REPORT_SHEET_ID:
        out["error"] = "ORM_REPORT_SHEET_ID is not set"
        return out
    # Guard (CLAUDE rule 1): the report scans the OLD sheet, which carries every
    # platform. If its source is ever the master (TP-only) sheet, SM/App/Esc come
    # back as a silent 0 — fail loud instead of posting a wrong, TP-only table.
    if ORM_MASTER_SHEET_ID and ORM_REPORT_SHEET_ID == ORM_MASTER_SHEET_ID:
        out["error"] = ("report source is the MASTER (TP-only) sheet — it would "
                        "count only Trustpilot. Set ORM_REPORT_SHEET_ID to the "
                        "old ORM RAW sheet.")
        return out
    try:
        from server.services.orm_raw_export import a1_sheet
        io = SheetIO(ORM_REPORT_SHEET_ID, ORM_REPORT_SHEET_TAB)
        tab = io.resolve_tab() if hasattr(io, "resolve_tab") else io.tab
        rows = io.read_range(f"{a1_sheet(tab)}!A2:I")
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        log.warning("[orm_report] sheet read failed: %s", e)
        return out
    counts, meta = count_rows(rows, day)
    out["counts"], out["meta"] = counts, meta
    out["text"] = render(counts, day, meta)
    return out
