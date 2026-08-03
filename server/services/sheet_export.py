"""Every review and its RCA, as rows in a Google Sheet.

The database is the only copy. `/api/reporting` returns aggregates — counts by
L1, average minutes to send — and there is no way to get at the rows
themselves, so nobody outside the dashboard can pivot, filter or hand a month
of RCAs to anyone. This is that export.

Three things it refuses to do quietly:

  * write into a sheet whose header does not match COLUMNS. A column added
    here and not there shifts every value one place left, and the sheet stays
    perfectly plausible — dates under "author", ids under "rating". That is
    unrecoverable by reading it, so it is checked before a single cell moves.
  * append a review that is already there. Re-running an RCA must update its
    row, not add a second one, or the sheet slowly fills with stale duplicates
    of the same review and no way to tell which is current.
  * report a partial write as a complete one. What could not be written is
    counted and named.

The HTTP layer is injected rather than imported, so the row building, the
header check and the upsert planning are all driveable without a live sheet —
which matters here, because docs.google.com is not reachable from every
environment this runs in.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)

SCOPE_RW = "https://www.googleapis.com/auth/spreadsheets"

# The header, and the only definition of it. Order is the sheet's column order;
# changing it is a breaking change to any sheet already populated, which is why
# check_header() exists.
COLUMNS = [
    "review_id", "received_at", "author", "rating", "language", "review_text",
    "status",
    "booking_id", "tid", "vid", "tgid", "experience", "vendor", "visit_date",
    "match_tier", "match_method",
    "l1", "l2", "sub_themes", "scenarios",
    "tldr_our_mistake", "tldr_our_fix",
    "issue_count", "issues", "owners", "claim_accuracy",
    "sop_verdict", "resolution", "takedown", "flags",
    "zendesk_tickets", "rca_posted_at", "sent_at",
    "final_response", "rca_prompt_version", "exported_at",
]


def _s(v) -> str:
    """A cell. Lists become "; "-joined, datetimes ISO, None empty.

    Never str(None) — "None" in a cell reads as a value somebody entered, and
    a spreadsheet has no other way to say "we did not have this".
    """
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat(sep=" ", timespec="minutes")
    if isinstance(v, (list, tuple)):
        return "; ".join(_s(x) for x in v if x is not None and x != "")
    if isinstance(v, dict):
        return json.dumps(v, ensure_ascii=False, default=str)[:2000]
    return str(v)


def row_for(review, draft, now: datetime | None = None) -> dict:
    """One review's row, as {column: value}.

    Takes the objects, not a session, so it can be driven with anything that
    has the attributes — and so a change to the columns is testable without a
    database.
    """
    d = draft
    v3 = (getattr(d, "rca_v3", None) or {}) if d else {}
    bk = (getattr(d, "booking", None) or {}) if d else {}
    tldr = v3.get("tldr") if isinstance(v3.get("tldr"), dict) else {}
    issues = (v3.get("what_went_wrong") or {}).get("guest_issues") or []
    sop = v3.get("sop_compliance") or {}
    takedown = v3.get("takedown") or {}

    return {
        "review_id":     getattr(review, "id", ""),
        "received_at":   getattr(review, "received_at", None),
        "author":        getattr(review, "author", ""),
        "rating":        getattr(review, "rating", None),
        "language":      getattr(review, "language", ""),
        "review_text":   (getattr(review, "body_english", None)
                          or getattr(review, "body_original", "") or ""),
        "status":        getattr(review, "status", ""),

        "booking_id":    bk.get("id") or bk.get("bid") or "",
        "tid":           bk.get("tid") or "",
        "vid":           bk.get("vid") or "",
        "tgid":          bk.get("tgid") or "",
        "experience":    bk.get("experienceName") or "",
        "vendor":        bk.get("vendorName") or "",
        "visit_date":    bk.get("date_of_visit") or bk.get("visitDate") or "",
        "match_tier":    getattr(d, "match_tier", None) if d else None,
        "match_method":  getattr(d, "match_method", "") if d else "",

        "l1":            getattr(d, "l1", "") if d else "",
        "l2":            getattr(d, "l2", "") if d else "",
        "sub_themes":    (getattr(d, "sub_themes", None) or []) if d else [],
        "scenarios":     (getattr(d, "scenarios", None) or []) if d else [],

        "tldr_our_mistake": tldr.get("our_mistake") or "",
        "tldr_our_fix":     tldr.get("our_fix") or "",

        # The count AND the titles. A count alone cannot be checked against the
        # card; the titles alone cannot be summed.
        "issue_count":   len(issues),
        "issues":        [i.get("issue") for i in issues if isinstance(i, dict)],
        "owners":        [i.get("owner") for i in issues
                          if isinstance(i, dict) and i.get("owner")],
        "claim_accuracy": [i.get("claim_accuracy") for i in issues
                           if isinstance(i, dict) and i.get("claim_accuracy")],

        "sop_verdict":   sop.get("verdict") or "",
        "resolution":    getattr(d, "resolution", "") if d else "",
        "takedown":      takedown.get("verdict") or "",
        "flags":         [f.get("flag") for f in (v3.get("flags") or [])
                          if isinstance(f, dict)],

        "zendesk_tickets": (getattr(d, "zendesk_ticket_ids", None) or []) if d else [],
        "rca_posted_at": getattr(d, "rca_posted_at", None) if d else None,
        "sent_at":       getattr(d, "sent_at", None) if d else None,

        "final_response": (getattr(d, "final_response", "")
                           or getattr(d, "suggested_response", "") or "") if d else "",
        "rca_prompt_version": getattr(d, "rca_prompt_version", "") if d else "",
        "exported_at":   now or datetime.utcnow(),
    }


def to_cells(row: dict) -> list[str]:
    """A row dict in COLUMNS order. Missing keys are empty, never dropped —
    dropping one would shift every later value into the wrong column."""
    return [_s(row.get(c)) for c in COLUMNS]


def check_header(existing_header: list[str]) -> str:
    """"" if the sheet is safe to write, or why it is not.

    An empty sheet is safe (the header gets written). A sheet whose header
    matches is safe. Anything else is refused: writing COLUMNS-ordered rows
    under a different header puts every value in the wrong column, and the
    result reads as plausible data rather than as an error.
    """
    have = [str(h).strip() for h in (existing_header or []) if str(h).strip()]
    if not have:
        return ""
    if have == COLUMNS:
        return ""
    missing = [c for c in COLUMNS if c not in have]
    extra = [c for c in have if c not in COLUMNS]
    parts = []
    if missing:
        parts.append(f"the sheet is missing {len(missing)} column(s): "
                     + ", ".join(missing[:8]))
    if extra:
        parts.append(f"it has {len(extra)} this build does not know: "
                     + ", ".join(extra[:8]))
    if not parts:
        parts.append("the columns are in a different order")
    return ("the sheet's header does not match this build — "
            + "; ".join(parts)
            + ". Writing anyway would put every value in the wrong column, so "
              "nothing was written. Fix the header row, or clear the sheet and "
              "let this rewrite it.")


def plan(existing_ids: list[str], rows: list[dict]) -> tuple[list, list]:
    """(updates, appends) for an upsert keyed on review_id.

    updates is [(row_number, cells)] with row_number 1-based INCLUDING the
    header, which is what the Sheets range syntax wants. appends is [cells].

    Re-running an RCA has to replace its row. Appending instead would fill the
    sheet with stale copies of the same review, all equally plausible, and the
    newest is not reliably the last one because a re-run of an old review lands
    after a fresh one.
    """
    # First occurrence wins: if the sheet already has duplicates, updating the
    # first and leaving the rest is wrong but stable, and it is reported.
    at = {}
    for i, rid in enumerate(existing_ids):
        rid = str(rid or "").strip()
        if rid and rid not in at:
            at[rid] = i + 2          # +1 for 0-based, +1 for the header row
    updates, appends = [], []
    for r in rows:
        cells = to_cells(r)
        n = at.get(str(r.get("review_id") or "").strip())
        (updates.append((n, cells)) if n else appends.append(cells))
    return updates, appends


class SheetIO:
    """The Sheets API, behind three methods.

    Injected rather than imported so everything above can be tested without a
    live sheet — which is not a convenience here: docs.google.com is
    unreachable from some of the environments this runs in, and a module that
    could only be exercised against the real thing would ship untested.
    """

    def __init__(self, sheet_id: str, tab: str = "Sheet1"):
        self.sheet_id = sheet_id
        self.tab = tab
        self._token = None

    # -- auth ---------------------------------------------------------------
    def _hdr(self):
        if self._token is None:
            from google.auth.transport.requests import Request as _Req
            from google.oauth2 import service_account
            from server.config import GCP_SERVICE_ACCOUNT_JSON
            if not GCP_SERVICE_ACCOUNT_JSON:
                raise RuntimeError(
                    "GCP_SERVICE_ACCOUNT_JSON is not set, so there is no "
                    "identity to write as. Reading uses the same credential; "
                    "writing additionally needs the sheet SHARED WITH THE "
                    "SERVICE ACCOUNT as an editor.")
            info = json.loads(GCP_SERVICE_ACCOUNT_JSON)
            # The read-write scope. Everything else in this codebase asks for
            # spreadsheets.readonly, and a readonly token fails the write with
            # a 403 that reads like a permission problem on the sheet.
            creds = service_account.Credentials.from_service_account_info(
                info, scopes=[SCOPE_RW])
            creds.refresh(_Req())
            self._token = creds.token
        return {"Authorization": f"Bearer {self._token}"}

    def _base(self):
        return f"https://sheets.googleapis.com/v4/spreadsheets/{self.sheet_id}"

    # -- the three operations ----------------------------------------------
    def read_column_a_and_header(self):
        """(header_row, review_ids_below_it)."""
        import httpx
        r = httpx.get(f"{self._base()}/values/{self.tab}!A1:ZZ1",
                      headers=self._hdr(), timeout=20.0)
        r.raise_for_status()
        header = (r.json().get("values") or [[]])[0]
        r2 = httpx.get(f"{self._base()}/values/{self.tab}!A2:A",
                       headers=self._hdr(), timeout=20.0)
        r2.raise_for_status()
        ids = [(row[0] if row else "") for row in (r2.json().get("values") or [])]
        return header, ids

    def write_header(self):
        import httpx
        r = httpx.put(f"{self._base()}/values/{self.tab}!A1",
                      headers=self._hdr(),
                      params={"valueInputOption": "RAW"},
                      json={"values": [COLUMNS]}, timeout=20.0)
        r.raise_for_status()

    def update_rows(self, updates):
        """One batch call, not one per row — a hundred round trips to update a
        hundred reviews is how an export becomes something nobody runs."""
        if not updates:
            return
        import httpx
        data = [{"range": f"{self.tab}!A{n}", "values": [cells]}
                for n, cells in updates]
        r = httpx.post(f"{self._base()}/values:batchUpdate",
                       headers=self._hdr(),
                       json={"valueInputOption": "RAW", "data": data},
                       timeout=60.0)
        r.raise_for_status()

    def append_rows(self, rows):
        if not rows:
            return
        import httpx
        r = httpx.post(f"{self._base()}/values/{self.tab}!A1:append",
                       headers=self._hdr(),
                       params={"valueInputOption": "RAW",
                               "insertDataOption": "INSERT_ROWS"},
                       json={"values": rows}, timeout=60.0)
        r.raise_for_status()


def export(io, rows, apply: bool = False) -> dict:
    """Upsert `rows` through `io`. Returns what happened, including what did not.

    Never raises for a refusal — the caller gets a report. A traceback is the
    wrong shape for "your sheet's header is wrong", which is a thing to read
    and fix, not a crash.
    """
    out = {"updated": 0, "appended": 0, "refused": "", "duplicates": [],
           "header_written": False, "rows": len(rows)}
    header, ids = io.read_column_a_and_header()
    why = check_header(header)
    if why:
        out["refused"] = why
        return out
    if not header:
        if apply:
            io.write_header()
        out["header_written"] = True
    out["duplicates"] = duplicate_ids(ids)
    updates, appends = plan(ids, rows)
    out["updated"], out["appended"] = len(updates), len(appends)
    if apply:
        io.update_rows(updates)
        io.append_rows(appends)
    return out


def duplicate_ids(existing_ids: list[str]) -> list[str]:
    """Review ids the sheet already holds more than once.

    Not fixed automatically — deleting somebody's rows is not this tool's call.
    Counted and named, because an upsert against a sheet with duplicates
    updates one of them and silently leaves the others looking current.
    """
    seen, dupes = set(), []
    for rid in existing_ids:
        rid = str(rid or "").strip()
        if not rid:
            continue
        if rid in seen and rid not in dupes:
            dupes.append(rid)
        seen.add(rid)
    return dupes
