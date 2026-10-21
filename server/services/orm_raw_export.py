"""Append one Trustpilot case to the team's ORM RAW sheet when it is solved.

SEPARATE FROM sheet_export.py ON PURPOSE. That module writes the full RCA dump
(review_id, case_findings, dss, every field) to the engineering export sheet,
keyed by review_id and upserted. THIS writes a single human-logging row — the
24 columns the ORM team fills by hand for social-media and app cases — to the
`ORM RAW` tab of the team dashboard, APPEND-ONLY, so a Trustpilot case we solve
lands beside the rows people log for the other platforms.

The two share the WRITE MACHINERY (`sheet_export.SheetIO`: service-account auth
with the spreadsheets write scope, append, the header read) and nothing else:
the column set, the mapping and the trigger are this sheet's own. Bending the
full export to also produce this shape is how one column drift would corrupt
both sheets at once.

Pure where it can be: `orm_row_for()` and `to_cells()` take the review/draft
objects, not a session, so the mapping is testable without a live sheet or a
database. Only `append_row()` touches the network.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from server.services.sheet_export import (SheetIO, _bkfield, _s, slack_link)
from server.services.slack import _action_team_label
from server.services.daily_report import IST

log = logging.getLogger("orm_raw_export")


def a1_sheet(name: str) -> str:
    """A sheet name as it must appear in an A1 range. 'ORM RAW' has a space, so
    the API needs it single-quoted — `'ORM RAW'!A1`, not `ORM RAW!A1`, which it
    misparses. A bare alphanumeric name is left as-is; anything else is quoted,
    with internal single quotes doubled per A1 rules."""
    n = str(name or "").strip()
    if n and n.replace("_", "").isalnum():
        return n
    return "'" + n.replace("'", "''") + "'"

# The ORM RAW header, EXACTLY as the sheet has it, in column order. A single
# character off (a trailing space, a different caption) makes check_header
# refuse the write rather than shift every value one column left — the same
# guard sheet_export uses. Read live from the sheet on 2026-10-21.
COLUMNS = [
    "Date", "Source", "Query", "Booking ID", "TGID", "Experience Name",
    "City", "Link to the review", "Owner", "Any follow-up needed?",
    "Comments", "Solution Offered", "What was the issue?",
    "Did we raise with the team?", "Link raised",
    "Was the issue solved?(If raised internally)", "Raised with Second team?",
    "Link Raised:", "Review Take down sent?",
    "Follow up from an untraceable review?",
    "If yes, What is the source of the review", "Month", "Year", "Week",
]

# Columns a human fills after the fact, left blank by the automation. Kept as a
# named set so "we chose to leave this blank" is distinguishable from "we forgot
# a column" — and so the test shows exactly which cells a person still owns.
HUMAN_FILLED = {
    "Any follow-up needed?", "Comments", "Link raised",
    "Was the issue solved?(If raised internally)", "Raised with Second team?",
    "Link Raised:", "Follow up from an untraceable review?",
    "If yes, What is the source of the review",
}


def _solve_dt(review, draft) -> datetime | None:
    """The moment the case was solved, as an IST-aware datetime.

    sent_at is when the reply went out / the RCA was posted / the case was
    closed — the "solved today" the daily report counts on. Stored naive UTC
    (like every timestamp in this codebase), so it is stamped UTC then moved to
    IST. Falls back to the review's own closed/received time when a draft has no
    sent_at, so a row still carries a date rather than a blank."""
    dt = (getattr(draft, "sent_at", None) if draft else None) \
        or getattr(review, "closed_at", None) \
        or getattr(review, "received_at", None)
    if not isinstance(dt, datetime):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(IST)


def _date_cell(ist: datetime | None) -> str:
    """"October 21, 2026" — the format the sheet's existing rows use. Built by
    hand because %-d / %#d is not portable across platforms."""
    if ist is None:
        return ""
    return f"{ist.strftime('%B')} {ist.day}, {ist.year}"


def _query(l1: str, l2: str) -> str:
    """Query is the classification — L1 and L2 — joined, blanks dropped."""
    parts = [p for p in (str(l1 or "").strip(), str(l2 or "").strip()) if p]
    return " / ".join(parts)


def _city_for(draft, bk: dict) -> str:
    """City from the Zendesk booking-details 'City' custom field.

    `ticket_signals()` reads that field as `city` (zendesk.py), and it is folded
    into the draft's ticket_facts. Read from there first; fall back to any city
    key the booking dict happens to carry. Blank when the ticket had none —
    never invented, because an invented city reads as a matched fact."""
    tf = (getattr(draft, "ticket_facts", None) or {}) if draft else {}
    if isinstance(tf, dict) and str(tf.get("city") or "").strip():
        return str(tf["city"]).strip()
    return _bkfield(bk, "city", "cityName", "experienceCity", "venue_city") or ""


def _raised_with_team(actions_taken) -> str:
    """The team(s) we escalated to, as the sheet's own labels.

    actions_taken is {team_key: [action, ...]}. A team with at least one action
    is one we raised with; its ACTION_TABS label ("Supply Partner", "CO team",
    "NA/Guest error") is exactly the sheet's dropdown value. Several teams join
    with ", "; none leaves it blank for a human to set."""
    at = actions_taken if isinstance(actions_taken, dict) else {}
    labels = []
    for team, items in at.items():
        if items:
            lbl = _action_team_label(team)
            if lbl and lbl not in labels:
                labels.append(lbl)
    return ", ".join(labels)


def orm_row_for(review, draft, now: datetime | None = None) -> dict:
    """One ORM RAW row as {column: value}. Objects in, no session, so the whole
    mapping is testable without a sheet."""
    d = draft
    v3 = {}
    if d is not None:
        try:
            from server.api import _resolve_v3_sections
            v3 = _resolve_v3_sections(d)[0] or {}
        except Exception as e:      # never fail a row over a projection
            log.warning("[orm_raw] v3 resolution failed, using raw blob: %s", e)
            v3 = getattr(d, "rca_v3", None) or {}
    bk = (getattr(d, "booking", None) or {}) if d else {}
    takedown = v3.get("takedown") or {}
    ist = _solve_dt(review, draft)

    row = {c: "" for c in COLUMNS}                      # blanks by default
    row.update({
        "Date":            _date_cell(ist),
        "Source":          "Trustpilot",
        "Query":           _query(getattr(d, "l1", "") if d else "",
                                  getattr(d, "l2", "") if d else ""),
        "Booking ID":      _bkfield(bk, "id", "bid", "booking_id"),
        "TGID":            _bkfield(bk, "tgid", "TGID", "tourGroupId"),
        "Experience Name": _bkfield(bk, "experienceName", "experience",
                                    "experience_name", "tourGroupName"),
        "City":            _city_for(d, bk),
        "Link to the review": slack_link(review),
        "Owner":           getattr(review, "picked_up_by", "") or "",
        "Solution Offered": (getattr(d, "resolution", "") if d else "") or "",
        "What was the issue?": v3.get("outcome_category") or "",
        "Did we raise with the team?": _raised_with_team(v3.get("actions_taken")),
        # The takedown dropdown value exactly as the dashboard holds it —
        # Yes / No / Untraceable — not re-interpreted.
        "Review Take down sent?": takedown.get("verdict") or "",
        "Month":           ist.strftime("%b") if ist else "",
        "Year":            str(ist.year) if ist else "",
        "Week":            str(ist.isocalendar()[1]) if ist else "",
    })
    return row


def to_cells(row: dict) -> list:
    """Row dict in COLUMNS order. Missing keys render blank, never dropped, so a
    short row can never shift the columns it does fill."""
    return [_s(row.get(c)) for c in COLUMNS]


def check_header(existing_header: list) -> str:
    """"" when the sheet's header is this build's COLUMNS; otherwise a sentence
    naming the mismatch. Writing COLUMNS-ordered rows under a different header
    puts every value one column off and stays plausible, so the write is refused
    instead — the same guard as sheet_export.check_header."""
    have = [str(h).strip() for h in (existing_header or []) if str(h).strip()]
    if not have:
        return ""                                       # empty sheet is safe
    if have == COLUMNS:
        return ""
    missing = [c for c in COLUMNS if c not in have]
    extra = [c for c in have if c not in COLUMNS]
    bits = []
    if missing:
        bits.append(f"missing {missing}")
    if extra:
        bits.append(f"unexpected {extra}")
    if not bits:
        bits.append("same columns, different order")
    return ("the ORM RAW header does not match this build — " + "; ".join(bits)
            + ". Nothing was written; align the header or the COLUMNS list.")


def append_row(review, draft, sheet_id: str, tab: str,
               now: datetime | None = None, apply: bool = False) -> dict:
    """Append one Trustpilot row to ORM RAW. Dry-run unless apply=True.

    Returns {planned, header_problem, wrote, row} so the caller (and the test
    endpoint) can see what WOULD be written and why a write was refused, rather
    than a silent empty sheet."""
    row = orm_row_for(review, draft, now=now)
    cells = to_cells(row)
    out = {"planned": dict(zip(COLUMNS, cells)), "wrote": False,
           "header_problem": "", "error": ""}
    if not apply:
        return out
    try:
        io = SheetIO(sheet_id, a1_sheet(tab))
        header, _ids = io.read_column_a_and_header()
        problem = check_header(header)
        if problem:
            out["header_problem"] = problem
            return out
        io.append_rows([cells])
        out["wrote"] = True
    except Exception as e:                              # a failed write is a finding
        out["error"] = f"{type(e).__name__}: {e}"
        log.warning("[orm_raw] append failed for %s: %s",
                    getattr(review, "id", "?"), e)
    return out


def write_solved_day(db, day: str = "", now: datetime | None = None,
                     apply: bool = False) -> dict:
    """Append an ORM RAW row for every Trustpilot case SOLVED on `day` (an IST
    calendar day; default today). Dry-run unless apply=True.

    "Solved" is the tool's own definition, shared with the daily digest:
    status=='sent' AND (draft.sent_at OR review.closed_at) inside the day, minus
    test rows — so a case closed either way counts, exactly as the Solved number
    on the report does. Runs against whatever DB the process is connected to, so
    it writes today's PRODUCTION rows only when run on the deployment."""
    from datetime import date as _date, timedelta
    from sqlalchemy import or_, and_
    from server.db import Review, RcaDraft
    from server.tiers import SENT
    from server.services.reporting_query import not_test_row_clause
    from server.config import ORM_RAW_SHEET_ID, ORM_RAW_SHEET_TAB

    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    if day:
        t = _date.fromisoformat(day)
        start_ist = datetime(t.year, t.month, t.day, tzinfo=IST)
    else:
        start_ist = now.astimezone(IST).replace(hour=0, minute=0, second=0,
                                                microsecond=0)
    start = start_ist.astimezone(timezone.utc).replace(tzinfo=None)
    end = (start_ist + timedelta(days=1)).astimezone(timezone.utc).replace(tzinfo=None)

    pairs = (db.query(Review, RcaDraft)
               .outerjoin(RcaDraft, RcaDraft.review_id == Review.id)
               .filter(Review.status == SENT)
               .filter(or_(and_(RcaDraft.sent_at >= start, RcaDraft.sent_at < end),
                           and_(Review.closed_at >= start, Review.closed_at < end)))
               .filter(not_test_row_clause(Review))
               .all())
    rows, wrote = [], 0
    for r, d in pairs:
        res = append_row(r, d, ORM_RAW_SHEET_ID, ORM_RAW_SHEET_TAB, apply=apply)
        p = res["planned"]
        rows.append({"review_id": getattr(r, "id", ""),
                     "owner": p.get("Owner"), "booking_id": p.get("Booking ID"),
                     "issue": p.get("What was the issue?"),
                     "wrote": res["wrote"],
                     "problem": res.get("error") or res.get("header_problem") or ""})
        if res.get("wrote"):
            wrote += 1
    return {"day": str(start_ist.date()), "solved": len(pairs), "wrote": wrote,
            "apply": apply, "rows": rows}


def on_review_finished(review, draft) -> dict:
    """Append this solved Trustpilot case to ORM RAW — ONLY when the automatic
    write is switched on. Off by default (ORM_RAW_EXPORT_ENABLED) so the mapping
    is confirmed on a test row before cases start flowing in live. Returns a
    'skipped' reason rather than writing when it is off, so the caller's log says
    which it was."""
    from server.config import (ORM_RAW_EXPORT_ENABLED, ORM_RAW_SHEET_ID,
                               ORM_RAW_SHEET_TAB)
    if not ORM_RAW_EXPORT_ENABLED:
        return {"wrote": False, "skipped": "ORM_RAW_EXPORT_ENABLED is off"}
    if not ORM_RAW_SHEET_ID:
        return {"wrote": False, "skipped": "ORM_RAW_SHEET_ID is not set"}
    return append_row(review, draft, ORM_RAW_SHEET_ID, ORM_RAW_SHEET_TAB,
                      apply=True)
