"""
Canned responses — Google Sheet lookup.

Fetches the canned responses sheet as CSV every 15 min (TTL cache).
Returns top-5 example responses ranked by relevance to L1/L2/sub_theme/review_text.

Used by response_draft_prompt as tone reference — NOT to be copied verbatim.
Falls back silently to [] when MOCK_MODE or the sheet is unreachable.
"""
import csv
import io
import logging
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


def _detect_cols(headers: list[str]) -> dict[str, int]:
    col: dict[str, int] = {}
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
            col.setdefault("response", i)
        if any(k in hl for k in ("l1", "category", "issue type")):
            col.setdefault("l1_hint", i)
        if any(k in hl for k in ("l2", "sub issue", "sub-issue")):
            col.setdefault("l2_hint", i)
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


async def _fetch_rows() -> list[dict]:
    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{CANNED_RESPONSES_SHEET_ID}"
        f"/export?format=csv&gid=0"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        r = await c.get(csv_url)
    raw: list[list[str]] = []
    ctype = r.headers.get("content-type", "")
    if r.status_code == 200 and "csv" in ctype.lower():
        raw = list(csv.reader(io.StringIO(r.text)))
    else:
        # An HTML body here is Google's login page: the sheet is private.
        import asyncio as _asyncio
        log.warning(
            f"[canned] CSV export not readable (HTTP {r.status_code}, {ctype}) - "
            f"the sheet is probably not link-viewable; trying the service account")
        raw = await _asyncio.to_thread(_rows_via_service_account)

    if not raw:
        log.warning("[canned] sheet returned no rows - responses will be drafted "
                    "with no tone reference")
        return []

    headers = [h.strip() for h in raw[0]]
    col = _detect_cols(headers)
    log.info(
        f"[canned] mapped columns: situation={col.get('situation', '?')}, "
        f"response={col.get('response', '?')}, tab=gid:0"
    )

    if "situation" not in col or "response" not in col:
        log.error(
            f"[canned] the sheet READ fine but its columns were not recognised. "
            f"Headers seen: {headers}. Needed: one column naming the situation "
            f"(situation/case/scenario/theme/issue/type/topic/category) and one "
            f"holding the reply (response/template/reply/message/text/macro/copy). "
            f"Every response will be drafted with no tone reference until this "
            f"matches.")
        return []

    sit_idx  = col["situation"]
    resp_idx = col["response"]

    rows: list[dict] = []
    for rw in raw[1:]:
        if max(sit_idx, resp_idx) >= len(rw):
            continue
        sit  = rw[sit_idx].strip()
        resp = rw[resp_idx].strip()
        if not sit or not resp:
            continue
        rows.append({
            "situation": sit,
            "response":  resp,
            "l1_hint":   rw[col["l1_hint"]].strip() if "l1_hint" in col and col["l1_hint"] < len(rw) else "",
            "l2_hint":   rw[col["l2_hint"]].strip() if "l2_hint" in col and col["l2_hint"] < len(rw) else "",
        })

    log.info(f"[canned] fetched {len(rows)} rows from sheet")
    for i, rw in enumerate(raw[1:4], 1):
        safe = [c[:40] if len(c) > 40 else c for c in rw[:4]]
        log.debug(f"[canned] sample row {i}: {safe}")
    return rows


async def _get_rows() -> list[dict]:
    global _cache_rows, _cache_at
    if _cache_rows and (time.time() - _cache_at) < _TTL:
        return _cache_rows
    try:
        rows = await _fetch_rows()
        _cache_rows = rows
        _cache_at = time.time()
    except Exception as e:
        # Silently returning [] means every response is drafted with no tone
        # reference and nobody knows why - name the sheet and the cause.
        log.error(f"[canned] sheet {CANNED_RESPONSES_SHEET_ID} unreadable: {e}. "
                  f"Responses will be drafted without a tone reference. Fix: "
                  f"share the sheet link-viewable, or with the service account.")
        if not _cache_rows:
            _cache_rows = []
    return _cache_rows


# ─── public API ─────────────────────────────────────────────────────────────

async def get_canned_responses(
    l1: str | None,
    l2: str | None,
    sub_theme: str | None,
    review_text: str,
) -> list[dict]:
    """
    Returns up to 5 example responses ranked by relevance.
    Shape: [{"situation": "...", "response": "..."}, ...]
    Used by response_draft_prompt as tone reference — NOT to copy verbatim.
    """
    if not is_live("canned"):
        return []

    rows = await _get_rows()
    if not rows:
        return []

    query_kw = _keywords(" ".join(filter(None, [l1, l2, sub_theme, review_text])))
    scored: list[tuple[int, dict]] = []

    for row in rows:
        sit_lower = row["situation"].lower()
        score = 0
        if l2 and l2.lower() in sit_lower:
            score += 4
        elif row.get("l2_hint") and l2 and l2.lower() in row["l2_hint"].lower():
            score += 3
        if l1 and l1.lower() in sit_lower:
            score += 2
        elif row.get("l1_hint") and l1 and l1.lower() in row["l1_hint"].lower():
            score += 2
        if sub_theme:
            code = sub_theme[:2].lower().strip(". ")
            label = sub_theme[2:].lower().strip()
            if code in sit_lower or label[:12] in sit_lower:
                score += 2
        overlap = len(query_kw & _keywords(row["situation"] + " " + row["response"]))
        score += overlap
        scored.append((score, row))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [
        {"situation": r["situation"], "response": r["response"]}
        for s, r in scored[:5]
        if s > 0
    ]
