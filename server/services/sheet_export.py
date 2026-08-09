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

# Required by google.oauth2 to build an identity at all. Checked here so a
# missing one is named rather than raised from inside the library.
_CRED_KEYS = ("client_email", "private_key", "token_uri")


def credential_problem(raw: str) -> str:
    """What is wrong with GCP_SERVICE_ACCOUNT_JSON, in words. "" if nothing.

    WHY THIS IS SEPARATE FROM THE SHARING CHECK. A credential that will not
    parse and a sheet that is not shared both surface as one thing from the
    outside — "could not read the spreadsheet" — and the fixes for them are
    opposite. This was not hypothetical: a placeholder pasted verbatim
    (`{"type":"service_account",...}`) raised a bare JSONDecodeError from
    json.loads, which arrived under the heading COULD NOT READ THE
    SPREADSHEET, and the advice printed underneath was to go share the sheet
    with the service account. The sharing was never the problem and the
    credential had never been read.

    So: everything knowable WITHOUT the network is decided here, and named.
    Anything this returns "" for has a real credential behind it, and a
    failure after that point genuinely is the sheet or the sharing.
    """
    raw = (raw or "").strip()
    if not raw:
        return ("GCP_SERVICE_ACCOUNT_JSON is not set, so there is no identity "
                "to write as. This is the secret to add; the sheet ALSO has "
                "to be shared with that identity as an editor.")
    if "..." in raw[:200]:
        # The placeholder in .env.example and in every set-up instruction ends
        # with `...`. Pasted as-is it is non-empty, so `creds set` reads true
        # and is_live() says the export is on.
        return ("GCP_SERVICE_ACCOUNT_JSON still contains '...', so what was "
                "pasted is the PLACEHOLDER, not a key. It has to be the whole "
                "service-account JSON, on one line.")
    try:
        info = json.loads(raw)
    except Exception as e:
        if not raw.endswith("}"):
            # A service-account file is pretty-printed over many lines. Pasted
            # into a .env, everything after the first newline is a separate
            # line the parser never sees.
            return (f"GCP_SERVICE_ACCOUNT_JSON is not valid JSON ({e}) and "
                    f"does not end in '}}', which is what a multi-line JSON "
                    f"file pasted into a single-line .env looks like. It has "
                    f"to be one line.")
        return (f"GCP_SERVICE_ACCOUNT_JSON is not valid JSON ({e}). This is "
                f"the value itself, not the sheet and not the sharing.")
    if not isinstance(info, dict):
        return (f"GCP_SERVICE_ACCOUNT_JSON parsed as {type(info).__name__}, "
                f"not an object. It has to be the service-account JSON.")
    missing = [k for k in _CRED_KEYS if not str(info.get(k) or "").strip()]
    if missing:
        return (f"GCP_SERVICE_ACCOUNT_JSON is missing {', '.join(missing)}. "
                f"That is not a whole service-account key — check nothing was "
                f"truncated on the way in.")
    if "PRIVATE KEY" not in info["private_key"]:
        return ("GCP_SERVICE_ACCOUNT_JSON has a private_key with no PEM "
                "header in it, so the key body did not survive the paste.")
    return ""


def _connector_available() -> bool:
    try:
        from server.services.sheets_connector import available
        return available()
    except Exception:
        return False


def auth_source() -> tuple[str, str]:
    """(which credential will be used, what is wrong with it). "" == fine.

    ONE DECISION, IN ONE PLACE. There are two ways to authenticate — the
    Replit Google Sheets connector, as BigQuery and Zendesk already do, and a
    service-account key — and the recurring defect in this project is the same
    rule implemented twice and drifting. _hdr(), the heartbeat and the
    preflight all ask this; none of them decides for itself.

    The connector wins when present because it has NO SHARING STEP: it is
    OAuth as the person who connected it, and they already own the sheet. A
    service account is a stranger to the Drive and needs the sheet shared with
    its client_email, which is the step that has been misdiagnosed twice.

    Returns ("none", why) when neither is usable — never ("", "").
    """
    if _connector_available():
        from server.services.sheets_connector import scope_problem
        return "connector", scope_problem()
    from server.config import GCP_SERVICE_ACCOUNT_JSON
    why = credential_problem(GCP_SERVICE_ACCOUNT_JSON)
    if not GCP_SERVICE_ACCOUNT_JSON:
        # BOTH ROUTES NAMED, because both are open and the easier one is not
        # the one the variable name points at.
        return "none", ("no Google credential: neither the Replit Google "
                        "Sheets connector (Tools → Connectors → Google "
                        "Sheets) nor GCP_SERVICE_ACCOUNT_JSON is set. The "
                        "connector needs no sharing step; the key does.")
    return "service_account", why

# The header, and the only definition of it. Order is the sheet's column order;
# changing it is a breaking change to any sheet already populated, which is why
# check_header() exists.
COLUMNS = [
    # `stage` IS THE FIRST THING YOU SCAN. A row written on arrival holds an
    # id, a time and a link and nothing else; a row written on send holds
    # everything. Without this column those two are told apart only by
    # squinting at which cells are blank — and a half-written row would read
    # exactly like an export that failed halfway.
    "review_id", "stage", "received_at", "slack_link",
    "author", "rating", "language", "review_text",
    "status",
    "booking_id", "tid", "vid", "tgid", "experience", "vendor", "visit_date",
    "match_tier", "match_method",
    "l1", "l2", "sub_themes", "scenarios",
    "issue_count", "issues", "owners", "claim_accuracy",
    "insights",
    "resolution", "takedown", "flags",
    "zendesk_tickets", "rca_posted_at", "sent_at",
    # WHICH KIND OF SENT. db.py records sent_route precisely because three
    # different pieces of work end at status="sent" — a reply that went out, an
    # RCA posted to the thread and marked finished, and a review closed out
    # with nothing to send. An export that shows only "sent" merges all three
    # and quietly overstates how many replies were written.
    "sent_route", "close_reason",
    "final_response", "rca_prompt_version", "exported_at",
    # LAST, AND USUALLY EMPTY. A row that could not be built lands here with
    # the reason in it rather than being dropped: a missing row and a row that
    # failed to build are different facts, and an export that silently drops
    # the second reports a smaller, cleaner month than actually happened.
    "export_error",
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


# The five the card's Experience insights panel shows, with the label it uses.
# Same five, same words: a sheet column and a card panel that disagree about
# what "Same-day issues" counts is a reconciliation nobody can win.
_INSIGHT_ROWS = (("tgidRating", "TGID rating"),
                 ("completion", "Completion rate"),
                 ("sameDayIssues", "Same-day issues"),
                 ("similarReviews", "Similar reviews"),
                 ("similarQueries", "Similar queries"))


def _insights_cell(ins) -> str:
    """The insights as one readable cell, or "".

    A JSON dump is not something anyone pivots on, and the raw block is
    nested. Flattened to "label value" pairs in the panel's order, dropping
    the ones the warehouse had nothing for — a cell of five em-dashes says
    less than an empty one.
    """
    if not isinstance(ins, dict):
        return ""
    out = []
    for key, label in _INSIGHT_ROWS:
        node = ins.get(key)
        val = (node or {}).get("value") if isinstance(node, dict) else node
        val = str(val or "").strip()
        if val and val != "\u2014":
            out.append(f"{label} {val}")
    return "; ".join(out)


def slack_link(review) -> str:
    """The permalink to the Slack message this review arrived on, or "".

    Built rather than stored: Slack permalinks are derivable from the channel
    and the ts, and a second stored copy of a derivable fact is one more thing
    to fall out of step. `C_MANUAL` and `VECTORSHIFT` are the synthetic
    channels for reviews that did not come from Slack at all — a link into
    them would 404, which is worse than no link.
    """
    ch = str(getattr(review, "slack_channel", "") or "").strip()
    ts = str(getattr(review, "slack_ts", "") or "").strip()
    if not ch or not ts or ch in ("C_MANUAL", "VECTORSHIFT"):
        return ""
    return f"https://slack.com/archives/{ch}/p{ts.replace('.', '')}"


def row_for(review, draft, now: datetime | None = None,
            stage: str = "") -> dict:
    """One review's row, as {column: value}.

    Takes the objects, not a session, so it can be driven with anything that
    has the attributes — and so a change to the columns is testable without a
    database.

    TWO PHASES, ONE BUILDER. Called with `draft=None` on arrival it produces
    the arrival shape — id, time, link, and the review itself — because every
    draft-derived field reads off `d` and `d` is None. A second builder would
    be a second place for a column to be forgotten.
    """
    d = draft
    v3 = (getattr(d, "rca_v3", None) or {}) if d else {}
    bk = (getattr(d, "booking", None) or {}) if d else {}
    issues = (v3.get("what_went_wrong") or {}).get("guest_issues") or []
    takedown = v3.get("takedown") or {}

    return {
        "review_id":     getattr(review, "id", ""),
        "stage":         stage,
        "received_at":   getattr(review, "received_at", None),
        "slack_link":    slack_link(review),
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

        # The count AND the titles. A count alone cannot be checked against the
        # card; the titles alone cannot be summed.
        "issue_count":   len(issues),
        "issues":        [i.get("issue") for i in issues if isinstance(i, dict)],
        "owners":        [i.get("owner") for i in issues
                          if isinstance(i, dict) and i.get("owner")],
        "claim_accuracy": [i.get("claim_accuracy") for i in issues
                           if isinstance(i, dict) and i.get("claim_accuracy")],

        # THE INSIGHTS AS A SENTENCE, not the raw block. The sheet is read
        # across rows; a JSON dump in a cell is not something anyone pivots on.
        "insights":      _insights_cell(getattr(d, "insights", None) if d else None),

        "resolution":    getattr(d, "resolution", "") if d else "",
        "takedown":      takedown.get("verdict") or "",
        "flags":         [f.get("flag") for f in (v3.get("flags") or [])
                          if isinstance(f, dict)],

        "zendesk_tickets": (getattr(d, "zendesk_ticket_ids", None) or []) if d else [],
        "rca_posted_at": getattr(d, "rca_posted_at", None) if d else None,
        "sent_at":       getattr(d, "sent_at", None) if d else None,
        # Off the REVIEW, not the draft: a review closed out with no RCA has
        # no draft to read this from, and that is exactly the case the column
        # exists to distinguish.
        "sent_route":    getattr(review, "sent_route", "") or "",
        "close_reason":  getattr(review, "close_reason", "") or "",

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


ARRIVED = "received"       # written when the review lands
DONE    = "sent"           # written when the RCA goes out or is closed out


def arrival_row(review, now: datetime | None = None) -> dict:
    """The row a review gets the moment it arrives: id, time, link, the text.

    Every draft-derived cell comes back empty because there is no draft yet,
    and `stage` says so. That is the point: a row a person opens ten minutes
    after the review landed should say "received", not look like an export
    that gave up halfway through a completed one.
    """
    return row_for(review, None, now=now, stage=ARRIVED)


def stage_for(review, draft) -> str:
    """ARRIVED or DONE for a review, derived rather than passed in.

    ANNOUNCING THE JUDGEMENT. The two hooks know their own phase because the
    caller is the event. A dump of every row has no event to read, so it
    decides — and the decision is `status == "sent"`, the same field the Sent
    tab uses, NOT the presence of a draft. A review can carry a draft for days
    before anything is sent; keying on the draft would report work as finished
    the moment someone opened it.
    """
    return DONE if (getattr(review, "status", "") or "") == "sent" else ARRIVED


def rows_for_all(pairs) -> tuple[list[dict], int]:
    """(rows, how_many_failed) for [(review, draft_or_None), ...].

    A row that raises is NOT dropped. It comes back carrying its review_id and
    the reason in `export_error`, because a review missing from the file and a
    review whose row blew up look identical once the file is open — and the
    second is a bug in here, not a quiet month.
    """
    rows, failed = [], 0
    for review, draft in pairs:
        rid = getattr(review, "id", "") or ""
        try:
            rows.append(row_for(review, draft, stage=stage_for(review, draft)))
        except Exception as e:
            failed += 1
            rows.append({"review_id": rid,
                         "export_error": f"{type(e).__name__}: {e}"})
    return rows, failed


def to_csv(rows: list[dict]) -> str:
    """COLUMNS as the header, then one line per row. RFC4180 quoting.

    csv.writer, not "," .join — review text carries commas, quotes and
    newlines, and a hand-rolled join turns one of those into a row that opens
    shifted by a column and still looks like data.
    """
    import csv
    import io as _io
    buf = _io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    for r in rows:
        w.writerow(to_cells(r))
    return buf.getvalue()


def plan(existing_ids: list[str], rows: list[dict],
         require_existing: bool = False) -> tuple[list, list, list]:
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
    updates, appends, orphans = [], [], []
    for r in rows:
        cells = to_cells(r)
        rid = str(r.get("review_id") or "").strip()
        n = at.get(rid)
        if n:
            updates.append((n, cells))
        elif require_existing:
            # THE COMPLETION WRITE NEVER APPENDS. Arrival creates the row, so
            # a completion that cannot find one means the arrival write did
            # not happen — and appending here would paper over that with a row
            # missing its arrival time, then race a late arrival write into a
            # duplicate. Named instead: two people finishing reviews at once
            # is the normal case this tool has to survive, and it survives it
            # by the ordering, not by luck.
            orphans.append(rid)
        else:
            appends.append(cells)
    return updates, appends, orphans


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

    def resolve_tab(self):
        """Turn a numeric tab into the sheet NAME the API needs.

        A URL carries a gid — `#gid=0` — and that is what anyone pastes. The
        Sheets values API takes a tab NAME in its A1 ranges and has no idea
        what a gid is, so a configured "0" would be read as a tab literally
        called 0 and every write would 400, or worse, land on a tab that
        happens to be named that.

        Resolved once, from the spreadsheet itself. If the gid is not there,
        the tab is left as configured and the caller gets the API's own error
        rather than a guess — inventing a fallback tab is how an export writes
        a month of rows into the wrong one.
        """
        tab = str(self.tab or "").strip()
        if not tab.lstrip("-").isdigit():
            return self.tab
        import httpx
        r = httpx.get(self._base(), headers=self._hdr(),
                      params={"fields": "sheets.properties(sheetId,title)"},
                      timeout=20.0)
        r.raise_for_status()
        for sh in (r.json().get("sheets") or []):
            props = sh.get("properties") or {}
            if str(props.get("sheetId")) == tab:
                self.tab = props.get("title") or self.tab
                log.info("[sheet] gid %s is the tab %r", tab, self.tab)
                return self.tab
        log.warning("[sheet] gid %s is not in this spreadsheet — leaving the "
                    "tab as %r", tab, self.tab)
        return self.tab

    # -- auth ---------------------------------------------------------------
    def _hdr(self):
        if self._token is None:
            # THE CHECK COMES FIRST, BEFORE THE LIBRARY IMPORT. google-auth is
            # not installed everywhere this runs, and importing it first meant
            # a bad credential reported ModuleNotFoundError — a third way for
            # the same misconfiguration to name the wrong culprit.
            src, why = auth_source()
            if why or src == "none":
                raise RuntimeError(why)
            if src == "connector":
                # OAuth as the person who connected it. No google-auth, no
                # key, and nothing to share — they already own the sheet.
                from server.services.sheets_connector import token
                self._token = token()
                return {"Authorization": f"Bearer {self._token}"}
            from server.config import GCP_SERVICE_ACCOUNT_JSON
            from google.auth.transport.requests import Request as _Req
            from google.oauth2 import service_account
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


def export(io, rows, apply: bool = False,
           require_existing: bool = False) -> dict:
    """Upsert `rows` through `io`. Returns what happened, including what did not.

    Never raises for a refusal — the caller gets a report. A traceback is the
    wrong shape for "your sheet's header is wrong", which is a thing to read
    and fix, not a crash.
    """
    out = {"updated": 0, "appended": 0, "refused": "", "duplicates": [],
           "header_written": False, "rows": len(rows), "orphans": []}
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
    updates, appends, orphans = plan(ids, rows, require_existing)
    out["updated"], out["appended"] = len(updates), len(appends)
    out["orphans"] = orphans
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


# ── the two hooks the app calls ────────────────────────────────────────────
#
# ONE ENTRY POINT, TWO STAGES. Both go through `_write` so the header check,
# the gid resolution and the refusal reporting cannot differ between them —
# and so "the export did nothing" has one place to be explained.
#
# NOTHING HERE RAISES INTO A REQUEST. A sheet that is unshared, renamed or
# rate-limited must not fail a send: the review going out matters and the row
# does not. Every outcome is logged with what would fix it.

def _write(review, draft, stage: str, require_existing: bool) -> dict:
    from server.config import (RCA_EXPORT_SHEET_ID, RCA_EXPORT_SHEET_TAB,
                               is_live)
    if not is_live("sheet_export"):
        # SAID, NOT SILENT. Unconfigured and broken must not look alike, and
        # this is the state every environment starts in.
        log.info("[sheet] not exporting %s: RCA_EXPORT_SHEET_ID or "
                 "GCP_SERVICE_ACCOUNT_JSON is unset",
                 getattr(review, "id", "?"))
        return {"skipped": "not configured"}
    row = (arrival_row(review) if stage == ARRIVED
           else row_for(review, draft, stage=DONE))
    try:
        io = SheetIO(RCA_EXPORT_SHEET_ID, RCA_EXPORT_SHEET_TAB)
        io.resolve_tab()
        out = export(io, [row], apply=True, require_existing=require_existing)
    except Exception as e:
        log.warning("[sheet] %s write failed for %s: %s — the review is "
                    "unaffected; the row is not there",
                    stage, getattr(review, "id", "?"), e)
        return {"failed": str(e)}
    if out.get("refused"):
        log.warning("[sheet] refused: %s", out["refused"])
    if out.get("orphans"):
        # The arrival write is what creates the row, and it did not happen.
        # Appending here would hide that AND race a late arrival into a
        # duplicate, so it is named instead.
        log.warning("[sheet] %s has no arrival row, so the completed row was "
                    "NOT written — check whether the arrival hook ran",
                    ", ".join(out["orphans"]))
    if out.get("duplicates"):
        log.warning("[sheet] the sheet already holds %s more than once; the "
                    "first was updated and the rest left as they are",
                    ", ".join(out["duplicates"]))
    log.info("[sheet] %s %s: updated=%s appended=%s",
             stage, getattr(review, "id", "?"), out.get("updated"),
             out.get("appended"))
    return out


def on_review_arrived(review) -> dict:
    """Phase one. Creates the row, so every later write is an UPDATE.

    That ordering is what makes this safe for several people at once: two
    completions racing are two updates to two different rows, and an update
    targets a row number that nothing shifts. Two appends racing would be the
    dangerous shape, and after this there are none.
    """
    return _write(review, None, ARRIVED, require_existing=False)


def on_review_finished(review, draft) -> dict:
    """Phase two — the RCA was sent, or the review was closed out."""
    return _write(review, draft, DONE, require_existing=True)
