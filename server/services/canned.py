"""
Canned responses — Google Sheet lookup.

Fetches the canned responses sheet as CSV every 15 min (TTL cache).
Returns top-5 example responses ranked by relevance to L1/L2/sub_theme/review_text.

Used by response_draft_prompt as tone reference — NOT to be copied verbatim.
Falls back silently to [] when MOCK_MODE or the sheet is unreachable.
"""
import csv
import io
import json
import logging
import pathlib
import re
import time

import httpx

from server.config import CANNED_RESPONSES_SHEET_ID, MOCK_MODE, is_live

log = logging.getLogger(__name__)

_TTL = 15 * 60  # seconds
_STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
              "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
              "not", "no", "do", "did", "has", "have", "had", "that", "this",
              "they", "them", "their", "which", "what", "when", "where", "who"}

_cache_rows: list[dict] = []
_cache_at: float = 0.0


# ─── helpers ────────────────────────────────────────────────────────────────

def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", text.lower())
    return {w for w in words if w not in _STOPWORDS}


# A response column holds a reply; a label column holds the name of one. Both
# match "response" on the header alone, so the header is not enough — the real
# sheet has a "Mode of Response" column ("Reply to cx post") sitting to the LEFT
# of "Content Approved Template Response", and first-match-wins picked the
# label. The model then got "Reply to cx post" as a tone example.
#
# Length settles it. Every approved macro in the live sheet runs to hundreds of
# characters; every label is a handful of words.
_MIN_RESPONSE_CHARS = 120


def _median_len(rows: list[list[str]], i: int) -> int:
    """Median cell length in a column, counting a blank as zero.

    Skipping blanks instead would let a column that is empty in most rows and
    holds one long note — "Takedown Macro" is exactly that shape — read as a
    column full of replies on the strength of the one. The question is whether
    this column is MOSTLY replies, so the empties have to count.
    """
    if not rows:
        return 0
    vals = sorted(len((r[i] or "").strip()) if i < len(r) else 0 for r in rows)
    return vals[len(vals) // 2]


def _pick_response_col(headers: list[str], rows: list[list[str]],
                       candidates: list[int]) -> int | None:
    """The column that actually holds replies, of the ones whose header says so.

    Ranked by what is IN the column, not by where it sits. Ties go to the
    header that names an approved reply over one that names a channel.
    """
    scored = []
    for i in candidates:
        med = _median_len(rows, i)
        if med < _MIN_RESPONSE_CHARS:
            continue
        hl = headers[i].lower() if i < len(headers) else ""
        bonus = 2 if "approved" in hl else (1 if "template" in hl or "macro" in hl else 0)
        scored.append((med + bonus * 50, i))
    return max(scored)[1] if scored else None


def _tab_is_a_mapping(headers: list[str], rows: list[list[str]],
                      col: dict) -> bool:
    """True for a tab that names issue types rather than holding replies.

    "Refer Macro Tags" is 75 rows of TP/SM/Twitter issue-type names side by
    side. Its header matches both keyword sets — "TP MACRO Issue Type" is a
    situation AND a macro — so it reads as 75 perfectly good canned responses
    whose text is a category label. Feeding those to the model as tone examples
    is worse than sending none: it is confidently wrong, and it looks like the
    sheet is working.
    """
    if "response" not in col:
        return True
    return _median_len(rows, col["response"]) < _MIN_RESPONSE_CHARS


def _detect_cols(headers: list[str], rows: list[list[str]] | None = None
                 ) -> dict[str, int]:
    col: dict[str, int] = {}
    cands: list[int] = []
    for i, h in enumerate(headers):
        hl = h.lower().strip()
        # "issue" and "type" belong here: the live sheet's header is
        # "Main Issue Type", which matched none of situation/case/scenario/theme,
        # so the sheet read fine and then every row was dropped for want of a
        # situation column - reported as "0 rows" as if the sheet were empty.
        if any(k in hl for k in ("situation", "case", "scenario", "theme",
                                 "issue", "type", "topic", "category")):
            col.setdefault("situation", i)
        if any(k in hl for k in ("response", "template", "reply", "message", "text",
                                 "macro", "copy")):
            # Every match is kept, not just the first. Which one actually holds
            # replies is decided by _pick_response_col from the column CONTENT,
            # because two columns on the same tab can both say "response" and
            # only one of them contains one.
            col.setdefault("response", i)
            cands.append(i)
        if any(k in hl for k in ("l1", "category", "issue type")):
            col.setdefault("l1_hint", i)
        if any(k in hl for k in ("l2", "sub issue", "sub-issue")):
            col.setdefault("l2_hint", i)
    # rows is optional so the header-only callers and their tests keep working;
    # without it there is nothing to measure and first-match stands.
    if rows and len(cands) > 1:
        best = _pick_response_col(headers, rows, cands)
        if best is not None:
            col["response"] = best
    return col


# ─── fetch ──────────────────────────────────────────────────────────────────

def _rows_via_service_account() -> list[list[str]]:
    """Read the sheet through the Sheets API as the BigQuery service account.
    The public CSV export needs the sheet to be link-viewable; a private sheet
    answers with a login page, and this is the way in without opening it to
    the world (share it with the service account's client_email as Viewer)."""
    import json as _json
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as _GARequest
    from server.config import GCP_SERVICE_ACCOUNT_JSON
    if not GCP_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("no GCP_SERVICE_ACCOUNT_JSON to authenticate with")
    creds = service_account.Credentials.from_service_account_info(
        _json.loads(GCP_SERVICE_ACCOUNT_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    creds.refresh(_GARequest())
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/"
           f"{CANNED_RESPONSES_SHEET_ID}/values/A:Z")
    r = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"},
                  timeout=15.0)
    if r.status_code != 200:
        raise RuntimeError(f"Sheets API HTTP {r.status_code} ({r.text[:120]})")
    return [[str(c) for c in row] for row in (r.json().get("values") or [])]


def _parse_tab(name: str, raw: list[list[str]]) -> tuple[list[dict], str]:
    """(rows, why-it-produced-none) for one tab's cells."""
    if len(raw) < 2:
        return [], f"{name}: empty"
    headers = [h.strip() for h in raw[0]]
    body = raw[1:]
    col = _detect_cols(headers, body)
    if "situation" not in col or "response" not in col:
        return [], (f"{name}: no situation/response columns in {headers[:4]}")
    if _tab_is_a_mapping(headers, body, col):
        return [], (f"{name}: reads as issue-type labels, not replies "
                    f"(median reply {_median_len(body, col['response'])} chars)")

    sit_idx, resp_idx = col["situation"], col["response"]
    rows: list[dict] = []
    for rw in body:
        if max(sit_idx, resp_idx) >= len(rw):
            continue
        sit, resp = rw[sit_idx].strip(), rw[resp_idx].strip()
        if not sit or not resp:
            continue
        rows.append({
            "situation": sit,
            "response":  resp,
            "tab":       name,
            "l1_hint":   rw[col["l1_hint"]].strip() if "l1_hint" in col and col["l1_hint"] < len(rw) else "",
            "l2_hint":   rw[col["l2_hint"]].strip() if "l2_hint" in col and col["l2_hint"] < len(rw) else "",
        })
    return rows, "" if rows else f"{name}: every row was missing a situation or a reply"


def _tabs_via_service_account() -> list[tuple[str, list[list[str]]]]:
    """Every tab, by name. `values/A:Z` with no sheet name reads only the FIRST
    one, which is how a nine-tab sheet was being read as one."""
    import json as _json
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as _GARequest
    from server.config import GCP_SERVICE_ACCOUNT_JSON
    if not GCP_SERVICE_ACCOUNT_JSON:
        raise RuntimeError("no GCP_SERVICE_ACCOUNT_JSON to authenticate with")
    creds = service_account.Credentials.from_service_account_info(
        _json.loads(GCP_SERVICE_ACCOUNT_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    creds.refresh(_GARequest())
    hdr = {"Authorization": f"Bearer {creds.token}"}
    base = f"https://sheets.googleapis.com/v4/spreadsheets/{CANNED_RESPONSES_SHEET_ID}"

    meta = httpx.get(f"{base}?fields=sheets.properties.title", headers=hdr,
                     timeout=15.0)
    if meta.status_code != 200:
        raise RuntimeError(f"Sheets API HTTP {meta.status_code} "
                           f"({meta.text[:120]})")
    titles = [sh["properties"]["title"]
              for sh in (meta.json().get("sheets") or [])]
    if not titles:
        raise RuntimeError("the sheet reports no tabs")

    out = []
    for t in titles:
        import urllib.parse
        r = httpx.get(f"{base}/values/{urllib.parse.quote(t)}!A:Z", headers=hdr,
                      timeout=15.0)
        if r.status_code != 200:
            log.warning(f"[canned] tab {t!r}: HTTP {r.status_code}")
            continue
        out.append((t, [[str(c) for c in row]
                        for row in (r.json().get("values") or [])]))
    return out


async def _fetch_rows() -> list[dict]:
    """Every tab of the sheet, not the first one.

    This read `gid=0` and `values/A:Z` — both of which mean "tab one" — against
    a sheet with nine tabs split BY CHANNEL: Trustpilot, social, Twitter,
    email. The dashboard drafts Trustpilot replies, so whether the tone
    reference was even the right channel came down to which tab happened to be
    first. Two of the nine also read as valid canned responses while holding
    issue-type labels rather than replies; _tab_is_a_mapping drops those,
    because confidently wrong tone examples are worse than none.
    """
    global _last_reason
    tabs: list[tuple[str, list[list[str]]]] = []
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{CANNED_RESPONSES_SHEET_ID}"
        f"/export?format=csv&gid=0"
    )
    try:
        import asyncio as _asyncio
        tabs = await _asyncio.to_thread(_tabs_via_service_account)
        log.info(f"[canned] read {len(tabs)} tab(s) via the service account: "
                 f"{[t for t, _ in tabs]}")
    except Exception as e:
        # The public CSV export can only reach tab one. Say so rather than let
        # a partial read look like a complete one.
        log.warning(f"[canned] service account unavailable ({e}); falling back "
                    f"to the public CSV export, which can only see the FIRST tab")
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
            r = await c.get(csv_url)
        ctype = r.headers.get("content-type", "")
        if r.status_code == 200 and "csv" in ctype.lower():
            tabs = [("gid:0 (first tab only)", list(csv.reader(io.StringIO(r.text))))]
        else:
            log.warning(
                f"[canned] CSV export not readable (HTTP {r.status_code}, {ctype}) "
                f"- the sheet is probably not link-viewable")

    if not tabs:
        return []

    rows: list[dict] = []
    skipped: list[str] = []
    for name, raw in tabs:
        got, why = _parse_tab(name, raw)
        rows.extend(got)
        if why:
            skipped.append(why)
        else:
            log.info(f"[canned] {name}: {len(got)} rows")

    # Rule 1 of this codebase: a tab we could not use has to be countable, or a
    # sheet where eight of nine tabs were dropped looks exactly like a sheet
    # with one tab.
    if skipped:
        log.warning(f"[canned] {len(skipped)} tab(s) contributed nothing: "
                    + " | ".join(skipped))
    if not rows:
        _last_reason = ("the sheet was read but no tab held usable replies: "
                        + "; ".join(skipped[:3]))
        return []
    # Cleared here, not only in _get_rows. A function that can SET a reason
    # and cannot clear one leaves the last failure attached to the next
    # success — and a stale reason is read as a current one.
    _last_reason = ""
    log.info(f"[canned] fetched {len(rows)} rows across "
             f"{len(tabs) - len(skipped)} of {len(tabs)} tab(s)")
    return rows


# Why the last read produced no rows, or "" when it produced some. The log
# already named the cause; this carries it to the reader of the draft, who is
# the person actually looking at a reply in the wrong voice. An unreachable
# sheet and a sheet with no matching row are the same empty list and entirely
# different problems - one is fixed by sharing a document, the other by
# writing a canned response.
_last_reason: str = ""
# WHICH source the current rows came from. Not a failure — the tone reference
# is present either way — but an edit someone made in the sheet that silently
# did not take effect is the same class of bug as everything else here, so the
# reader is told which copy they are looking at.
_last_source: str = ""


def last_source() -> str:
    """"the live sheet", or the checked-in copy and why the sheet was not used."""
    return _last_source


def last_failure_reason() -> str:
    """Why the sheet produced nothing, or "" if it produced rows.

    is_live("canned") is `bool(CANNED_RESPONSES_SHEET_ID)` - it says a sheet is
    configured, not that it can be read. Those came out identical at every
    caller, so a sheet nobody had shared looked exactly like a category nobody
    had written a reply for.
    """
    return _last_reason


VENDORED = pathlib.Path(__file__).resolve().parent.parent / "data" / "canned_macros.json"


def _rows_from_vendored() -> list[dict]:
    """The approved macros, checked into the repo.

    THIS IS THE SOURCE OF TRUTH. The live sheet was never a dependency worth
    having: it needs a service account share nobody had done, its id is
    ambiguous between config.py and .env.example, the public CSV export can
    only reach one of nine tabs, and every one of those failures came out as
    the same empty list — a reply drafted in the model's own voice with nothing
    on screen to say why.

    The macros are approved content that changes rarely and is reviewed when it
    does. Vendoring them means the tone reference ALWAYS works, offline, in
    CI, and in a deployment nobody has shared a document with. Refresh it by
    re-exporting the sheet to HTML and re-running tools/import_macros.py.
    """
    if not VENDORED.exists():
        return []
    tabs = json.loads(VENDORED.read_text(encoding="utf-8"))
    rows: list[dict] = []
    skipped: list[str] = []
    for name, raw in tabs.items():
        got, why = _parse_tab(name, raw)
        rows.extend(got)
        if why:
            skipped.append(why)
    if skipped:
        log.info(f"[canned] vendored: {len(skipped)} tab(s) hold no replies: "
                 + " | ".join(skipped))
    return rows


async def _get_rows() -> list[dict]:
    """The vendored macros, refreshed from the live sheet when that is possible.

    The sheet is an OPTIONAL improvement now, not a dependency. If it is
    reachable and yields more rows, they win — someone editing the sheet
    should see their edit. If it is not, the vendored copy carries the run and
    the reason the sheet failed is still recorded, because "we are on the
    checked-in copy" is a fact the reader should have.
    """
    global _cache_rows, _cache_at, _last_reason, _last_source
    if _cache_rows and (time.time() - _cache_at) < _TTL:
        return _cache_rows

    vendored = _rows_from_vendored()
    live: list[dict] = []
    sheet_why = ""
    if is_live("canned"):
        try:
            live = await _fetch_rows()
            sheet_why = _last_reason
        except Exception as e:
            sheet_why = (f"the live sheet could not be read "
                         f"({type(e).__name__}) — running on the checked-in "
                         f"macros instead")
            log.warning(f"[canned] {sheet_why}")
    else:
        # Just the fact. The caller prefixes "the checked-in macros", so
        # repeating it here rendered "from the checked-in macros — no live
        # sheet is configured — running on the checked-in macros" on a real
        # card. Twice is not clearer than once.
        sheet_why = "no live sheet is configured"

    if live:
        _cache_rows, _last_reason = live, ""
        _last_source = "the live sheet"
        log.info(f"[canned] {len(live)} replies from the live sheet")
    elif vendored:
        _cache_rows = vendored
        # NOT a failure. The tone reference is present and approved; only the
        # refresh did not happen. Marking this as a failure would make a
        # healthy run look broken, which is the inverse bug.
        _last_reason = ""
        _last_source = ("the checked-in macros"
                        + (f" — {sheet_why}" if sheet_why else ""))
        log.info(f"[canned] {len(vendored)} replies from the checked-in macros"
                 + (f" ({sheet_why})" if sheet_why else ""))
    else:
        _cache_rows = []
        _last_source = ""
        _last_reason = (sheet_why or "no live sheet") + \
            f" and the checked-in macros are missing from {VENDORED.name}"
        log.error(f"[canned] {_last_reason}")
    _cache_at = time.time()
    return _cache_rows


def vendored_status() -> str:
    """One line for tools/doctor.py: what the checked-in copy holds."""
    if not VENDORED.exists():
        return f"missing — {VENDORED} is not in this tree"
    rows = _rows_from_vendored()
    tabs = sorted({r["tab"] for r in rows})
    return f"{len(rows)} replies across {len(tabs)} tab(s): {', '.join(tabs)}"


# ─── public API ─────────────────────────────────────────────────────────────

# The channel this dashboard drafts for. The macros are split by channel and
# the voices genuinely differ - a Twitter reply is 280 characters and a
# Trustpilot one is a paragraph - so a macro from the wrong tab is the wrong
# answer even when its situation matches perfectly.
TP_TAB_HINT = "TP"

# Below this, no approved macro applies. The old bar was score > 0, which one
# shared word clears - so a reply about a meeting point could be "matched" to
# a refund macro on the word "booking" and go out looking approved. Above the
# bar means a real L2 or L1 hit (worth 4 and 2) or a strong situation overlap;
# below it means say so and let a human write it.
MATCH_MIN = 4


def channel_issue_types() -> dict[str, list[str]]:
    """The approved issue-type vocabulary, per channel, from the tags tab.

    "Refer Macro Tags" is not a list of replies - it is the taxonomy the macros
    are named by, three columns of it, one per channel. Parsed as replies it
    poisons the tone reference; parsed as what it is, it is the only thing that
    says which issue types are real. 72 of its 75 TP entries match a macro's
    Use Case exactly, so it is also a check on drift between the two.
    """
    if not VENDORED.exists():
        return {}
    tabs = json.loads(VENDORED.read_text(encoding="utf-8"))
    raw = tabs.get("Refer Macro Tags") or []
    if len(raw) < 2:
        return {}
    headers = [h.strip() for h in raw[0]]
    out: dict[str, list[str]] = {}
    for i, h in enumerate(headers):
        if not h:
            continue
        vals = [r[i].strip() for r in raw[1:] if i < len(r) and r[i].strip()]
        if vals:
            out[h] = vals
    return out


def _stem(w: str) -> str:
    """Crudest possible: drop a trailing s. "Meeting Point Issues" has to match
    "Meeting point issue//" and an exact-substring test does not, which is how
    a perfectly good macro scored zero."""
    return w[:-1] if len(w) > 4 and w.endswith("s") else w


def _toks(text: str) -> set[str]:
    return {_stem(w) for w in _keywords(text or "")}


def _score_row(row: dict, l1, l2, sub_theme, review_kw) -> int:
    """How well an approved macro fits this review.

    Scored on the SITUATION, not the reply body. Every macro shares the same
    boilerplate — "sorry", "booking", "team" — so scoring against the body let
    that boilerplate carry a match and everything looked equally relevant.

    Token overlap rather than substring: the classification says "Meeting Point
    Issues" and the macro is filed under "SP issue - Meeting point issue//
    Venue Related Issue", which share every meaningful word and no substring.
    """
    sit = _toks(row["situation"])
    if not sit:
        return 0
    score = 0
    for text, weight in ((l2, 3), (l1, 2), (sub_theme, 2)):
        if not text:
            continue
        t = _toks(text)
        if t:
            score += weight * len(t & sit)
    for hint, weight in (("l2_hint", 2), ("l1_hint", 1)):
        h = _toks(row.get(hint) or "")
        if h and l2 and (_toks(l2) & h):
            score += weight
    score += len(review_kw & sit)
    return score


# The macro for a review whose booking we could not find. It exists in the TP
# tab and the untraceable path could not reach it: an untraceable review has no
# booking, so usually no classification, so nothing to match on — and the words
# "unable to trace" are OURS, not the guest's. The guest just complains; we are
# the ones who cannot find the booking. Keyword matching was never going to
# bridge that, so the state has to be passed in.
_TRACE_MACRO = "unable to trace"


async def get_canned_responses(
    l1: str | None,
    l2: str | None,
    sub_theme: str | None,
    review_text: str,
    untraceable: bool = False,
) -> list[dict]:
    """
    Approved macros that actually apply, best first, or [] when none do.

    [] is a real answer now, not a degraded one. It used to mean "write in
    plain warm English" and the model would invent a reply that read as though
    it had been approved. It now means the associate writes it - see
    MATCH_MIN, and the prompt rule that forbids inventing one.
    """
    global _last_reason
    rows = await _get_rows()
    if not rows:
        return []

    # No booking found. There is a macro written for exactly this and it is the
    # right answer whatever the review says, so it is selected directly rather
    # than competed for on keywords it would lose.
    if untraceable:
        trace = [r for r in rows
                 if _TRACE_MACRO in (r["situation"] or "").lower()]
        on_tp = [r for r in trace if TP_TAB_HINT in (r.get("tab") or "")]
        picked = on_tp or trace
        if picked:
            return [{"situation": r["situation"], "response": r["response"],
                     "tab": r.get("tab", ""), "score": None,
                     "why": "no booking was matched to this review"}
                    for r in picked[:3]]
        # It is not there. Fall through to normal matching rather than return
        # nothing — but this is worth knowing, because the untraceable branch
        # is now relying on a macro by name.
        log.warning(f"[canned] untraceable, but no macro named "
                    f"{_TRACE_MACRO!r} exists in the checked-in copy")

    review_kw = _toks(review_text)
    scored = [(_score_row(r, l1, l2, sub_theme, review_kw), r) for r in rows]

    # Channel first. A Trustpilot macro at the bar beats a Twitter one above
    # it, because the wrong voice is the wrong answer.
    def _rank(pair):
        sc, r = pair
        on_channel = TP_TAB_HINT in (r.get("tab") or "")
        return (on_channel, sc)

    scored = [p for p in scored if p[0] >= MATCH_MIN]
    scored.sort(key=_rank, reverse=True)
    return [{"situation": r["situation"], "response": r["response"],
             "tab": r.get("tab", ""), "score": sc}
            for sc, r in scored[:5]]
