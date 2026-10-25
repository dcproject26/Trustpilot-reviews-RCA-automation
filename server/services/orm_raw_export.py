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

# The MASTER sheet header (27 cols): cols 0-20 identical to COLUMNS, then three
# takedown/time columns the master sheet added, then Month/Year/Week (which the
# master sheet ALSO keeps, unlike what an earlier read suggested).
MASTER_COLUMNS = COLUMNS[:21] + [
    "Why was review takedown not sent?", "TP link sent for Takedown",
    "Time When the review was reacted to", "Month", "Year", "Week",
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
    """The City/country, the same value the card shows under "Extracted from
    review".

    It is the review indicator `city_or_country`, stored on the draft at
    extracted_signals.match_indicators (the card reads exactly this:
    `_sig.match_indicators.city_or_country`). Falls back to the Zendesk
    booking-details city (ticket_facts) and then any city key the booking dict
    carries. Blank when none — never invented."""
    # Zendesk booking-details city first — it is the actual city, where the
    # review indicator is often only a country ("Japan"). Fall back to the
    # review's city_or_country indicator (what the card shows), then the booking.
    tf = (getattr(draft, "ticket_facts", None) or {}) if draft else {}
    if isinstance(tf, dict) and str(tf.get("city") or "").strip():
        return str(tf["city"]).strip()
    sig = (getattr(draft, "extracted_signals", None) or {}) if draft else {}
    ind = sig.get("match_indicators") if isinstance(sig, dict) else None
    if isinstance(ind, dict) and str(ind.get("city_or_country") or "").strip():
        return str(ind["city_or_country"]).strip()
    return _bkfield(bk, "city", "cityName", "experienceCity", "venue_city") or ""


def _solution_for(v3: dict, draft) -> str:
    """Solution Offered — the resolution the card shows, not the empty free-text.

    The card's RESOLUTION dropdown is the STRUCTURED resolution
    (v3.resolution = {compensation_type, amount, text}); its compensation_type
    is the dropdown value ("Refund to card", "HOC", …). Render that, with the
    amount when there is one. The one-line free text is the fallback, and the
    legacy draft.resolution column is the last resort — that column was blank on
    most cards, which is why the structured type is read first."""
    res = v3.get("resolution")
    if isinstance(res, dict):
        ctype = str(res.get("compensation_type") or "").strip()
        amount = res.get("amount")
        text = str(res.get("text") or "").strip()
        if ctype and amount not in (None, "", 0, "0"):
            return f"{ctype} — {amount}"
        if ctype:
            return ctype
        if text:
            return text
        return ""
    if res:
        return str(res).strip()
    return (getattr(draft, "resolution", "") if draft else "") or ""


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
    # An untraceable review has no booking to log. Instead of leaving Booking ID
    # blank, write "Untraceable" — the takedown verdict is the untraceable marker.
    booking_id = _bkfield(bk, "id", "bid", "booking_id")
    if str(takedown.get("verdict") or "").strip().lower() == "untraceable":
        booking_id = "Untraceable"

    row = {c: "" for c in COLUMNS}                      # blanks by default
    row.update({
        "Date":            _date_cell(ist),
        "Source":          "Trustpilot",
        "Query":           _query(getattr(d, "l1", "") if d else "",
                                  getattr(d, "l2", "") if d else ""),
        "Booking ID":      booking_id,
        "TGID":            _bkfield(bk, "tgid", "TGID", "tourGroupId"),
        "Experience Name": _bkfield(bk, "experienceName", "experience",
                                    "experience_name", "tourGroupName"),
        "City":            _city_for(d, bk),
        "Link to the review": slack_link(review),
        "Owner":           getattr(review, "picked_up_by", "") or "",
        "Solution Offered": _solution_for(v3, d),
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
               now: datetime | None = None, apply: bool = False,
               existing_links: set | None = None) -> dict:
    """Append one Trustpilot row to ORM RAW. Dry-run unless apply=True.

    DEDUP BY THE REVIEW'S SLACK LINK. Column H (Link to the review) is a
    permalink unique to the review, so a case that is already on the sheet is
    skipped — whether it got there from the write-on-send hook or the 8pm
    backfill, and however many times an RCA is re-sent. `existing_links` lets a
    batch read the column once and share it (and it is updated as rows are
    written, so a batch never duplicates within itself).

    Returns {planned, header_problem, wrote, skipped, error} so the caller can
    see what WOULD be written and why a write was skipped or refused."""
    row = orm_row_for(review, draft, now=now)
    cells = to_cells(row)
    out = {"planned": dict(zip(COLUMNS, cells)), "wrote": False,
           "skipped": "", "header_problem": "", "error": ""}
    if not apply:
        return out
    try:
        io = SheetIO(sheet_id, a1_sheet(tab))
        header, _ids = io.read_column_a_and_header()
        problem = check_header(header)
        if problem:
            out["header_problem"] = problem
            return out
        link = (row.get("Link to the review") or "").strip()
        if link:
            if existing_links is None:
                existing_links = {str(r[0]).strip()
                                  for r in io.read_range(f"{a1_sheet(tab)}!H2:H")
                                  if r and r[0]}
            if link in existing_links:
                out["skipped"] = "already on the sheet (same review link)"
                return out
        io.append_rows([cells])
        if link and isinstance(existing_links, set):
            existing_links.add(link)
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
    # Read the sheet's review-links once so the batch dedups against what is
    # already there (and against itself) without a read per row.
    existing_links = None
    if apply and pairs:
        try:
            _io = SheetIO(ORM_RAW_SHEET_ID, a1_sheet(ORM_RAW_SHEET_TAB))
            existing_links = {str(x[0]).strip()
                              for x in _io.read_range(f"{a1_sheet(ORM_RAW_SHEET_TAB)}!H2:H")
                              if x and x[0]}
        except Exception as e:
            log.warning("[orm_raw] could not read existing links for dedup: %s", e)
            existing_links = None

    rows, wrote, skipped = [], 0, 0
    for r, d in pairs:
        res = append_row(r, d, ORM_RAW_SHEET_ID, ORM_RAW_SHEET_TAB, apply=apply,
                         existing_links=existing_links)
        p = res["planned"]
        rows.append({"review_id": getattr(r, "id", ""),
                     "owner": p.get("Owner"), "booking_id": p.get("Booking ID"),
                     "issue": p.get("What was the issue?"),
                     "wrote": res["wrote"], "skipped": res.get("skipped") or "",
                     "problem": res.get("error") or res.get("header_problem") or ""})
        if res.get("wrote"):
            wrote += 1
        elif res.get("skipped"):
            skipped += 1
    return {"day": str(start_ist.date()), "solved": len(pairs), "wrote": wrote,
            "skipped": skipped, "apply": apply, "rows": rows}


def on_review_finished(review, draft) -> dict:
    """Append this solved Trustpilot case to ORM RAW — ONLY when the automatic
    write is switched on. Off by default (ORM_RAW_EXPORT_ENABLED) so the mapping
    is confirmed on a test row before cases start flowing in live. Returns a
    'skipped' reason rather than writing when it is off, so the caller's log says
    which it was."""
    from server.config import (ORM_RAW_EXPORT_ENABLED, ORM_MASTER_EXPORT_ENABLED,
                               ORM_MASTER_SHEET_ID)
    if not ORM_RAW_EXPORT_ENABLED:
        return {"wrote": False, "skipped": "ORM_RAW_EXPORT_ENABLED is off"}
    # TP logging goes to the NEW master sheet ONLY. The old sheet is populated
    # from the new sheet by its own formula and is read by the 8pm report, so the
    # tool must NOT also write TP to it (that would double the rows).
    if not (ORM_MASTER_EXPORT_ENABLED and ORM_MASTER_SHEET_ID):
        return {"wrote": False, "skipped": "ORM_MASTER_EXPORT_ENABLED is off"}
    try:
        res = append_master_row(review, draft, apply=True)
        if res.get("error") or res.get("header_problem"):
            log.warning("[orm_master] not written for %s: %s",
                        getattr(review, "id", "?"),
                        res.get("error") or res.get("header_problem"))
        return res
    except Exception as e:
        log.warning("[orm_master] write failed for %s: %s",
                    getattr(review, "id", "?"), e)
        return {"wrote": False, "error": f"{type(e).__name__}: {e}"}


def _write_master_isolated(review, draft) -> None:
    """Dual-write to the master sheet AFTER the old-sheet write. Fully isolated:
    any failure is logged and swallowed so it can never break the old write or
    the solve path."""
    try:
        from server.config import (ORM_MASTER_EXPORT_ENABLED,
                                   ORM_MASTER_SHEET_ID)
        if ORM_MASTER_EXPORT_ENABLED and ORM_MASTER_SHEET_ID:
            res = append_master_row(review, draft, apply=True)
            if res.get("error") or res.get("header_problem"):
                log.warning("[orm_master] not written for %s: %s",
                            getattr(review, "id", "?"),
                            res.get("error") or res.get("header_problem"))
    except Exception as e:
        log.warning("[orm_master] write failed for %s: %s",
                    getattr(review, "id", "?"), e)


# ── MASTER sheet (additive dual-write) ──────────────────────────────────────

def master_row_for(review, draft, now: datetime | None = None) -> dict:
    """One MASTER-sheet row as {column: value}. Cols 0-20 are exactly
    orm_row_for's; the last three are the master sheet's own."""
    base = orm_row_for(review, draft, now=now)
    row = {c: base.get(c, "") for c in MASTER_COLUMNS[:21]}
    v3 = {}
    if draft is not None:
        try:
            from server.api import _resolve_v3_sections
            v3 = _resolve_v3_sections(draft)[0] or {}
        except Exception:
            v3 = getattr(draft, "rca_v3", None) or {}
    takedown = v3.get("takedown") or {}
    ist = _solve_dt(review, draft)
    row["Why was review takedown not sent?"] = takedown.get("reason") or ""
    row["TP link sent for Takedown"] = getattr(review, "review_url", None) or ""
    row["Time When the review was reacted to"] = (
        ist.strftime("%d %b %Y, %H:%M:%S") if ist else "")
    # Month/Year/Week come straight from the base row (orm_row_for computes them).
    row["Month"] = base.get("Month", "")
    row["Year"] = base.get("Year", "")
    row["Week"] = base.get("Week", "")
    return row


def master_to_cells(row: dict) -> list:
    return [_s(row.get(c)) for c in MASTER_COLUMNS]


def check_master_header(existing_header: list) -> str:
    have = [str(h).strip() for h in (existing_header or []) if str(h).strip()]
    if not have or have == MASTER_COLUMNS:
        return ""
    return ("the master sheet header does not match this build. Nothing was "
            "written; align the header or MASTER_COLUMNS.")


def append_master_row(review, draft, now: datetime | None = None,
                      apply: bool = False,
                      existing_links: set | None = None) -> dict:
    """append_row for the MASTER sheet. Dedup by the review's Link column."""
    from server.config import ORM_MASTER_SHEET_ID, ORM_MASTER_SHEET_TAB
    tab = ORM_MASTER_SHEET_TAB
    row = master_row_for(review, draft, now=now)
    cells = master_to_cells(row)
    out = {"planned": dict(zip(MASTER_COLUMNS, cells)), "wrote": False,
           "skipped": "", "header_problem": "", "error": ""}
    if not apply:
        return out
    try:
        io = SheetIO(ORM_MASTER_SHEET_ID, a1_sheet(tab))
        header, _ids = io.read_column_a_and_header()
        problem = check_master_header(header)
        if problem:
            out["header_problem"] = problem
            return out
        link = (row.get("Link to the review") or "").strip()
        if link:
            if existing_links is None:
                existing_links = {str(r[0]).strip()
                                  for r in io.read_range(f"{a1_sheet(tab)}!H2:H")
                                  if r and r[0]}
            if link in existing_links:
                out["skipped"] = "already on the sheet (same review link)"
                return out
        io.append_rows([cells], value_input_option="USER_ENTERED")
        if link and isinstance(existing_links, set):
            existing_links.add(link)
        out["wrote"] = True
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        log.warning("[orm_master] append failed for %s: %s",
                    getattr(review, "id", "?"), e)
    return out


def write_solved_day_master(db, day: str = "", now: datetime | None = None,
                            apply: bool = False) -> dict:
    """write_solved_day for the MASTER sheet: same solved cohort, deduped."""
    from datetime import date as _date, timedelta
    from sqlalchemy import or_, and_
    from server.db import Review, RcaDraft
    from server.tiers import SENT
    from server.services.reporting_query import not_test_row_clause
    from server.config import ORM_MASTER_SHEET_ID, ORM_MASTER_SHEET_TAB

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
    existing_links = None
    if apply and pairs:
        try:
            _io = SheetIO(ORM_MASTER_SHEET_ID, a1_sheet(ORM_MASTER_SHEET_TAB))
            existing_links = {str(x[0]).strip()
                              for x in _io.read_range(f"{a1_sheet(ORM_MASTER_SHEET_TAB)}!H2:H")
                              if x and x[0]}
        except Exception as e:
            log.warning("[orm_master] could not read existing links: %s", e)
    rows, wrote, skipped = [], 0, 0
    for r, d in pairs:
        res = append_master_row(r, d, apply=apply, existing_links=existing_links)
        p = res["planned"]
        rows.append({"review_id": getattr(r, "id", ""),
                     "owner": p.get("Owner"), "booking_id": p.get("Booking ID"),
                     "wrote": res["wrote"], "skipped": res.get("skipped") or "",
                     "problem": res.get("error") or res.get("header_problem") or ""})
        if res.get("wrote"):
            wrote += 1
        elif res.get("skipped"):
            skipped += 1
    return {"day": str(start_ist.date()), "solved": len(pairs), "wrote": wrote,
            "skipped": skipped, "apply": apply, "rows": rows}
