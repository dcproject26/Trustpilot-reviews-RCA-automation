"""
DSS — Google Sheet lookup (CE/RO path only).

Fetches the Decision Support Sheet as CSV every 15 min (TTL cache).
Column names are detected heuristically from the header row.
Escalations rows are skipped; only CE / RO / empty-team rows are kept.
Scores each row against L1/L2/review_text and returns the best match.

Apps Script: if DSS_APPS_SCRIPT_URL env var is set and returns a 200 with a
JSON list, that path is used in preference to CSV export.

Fallback: when MOCK_MODE or the sheet fetch fails, returns MOCK_DSS[review_id].
"""
import csv
import io
import logging
import os
import re
import time

import httpx

from server.config import (
    DSS_SHEET_ID, DSS_SHEET_TAB, GOOGLE_API_KEY, MOCK_MODE, is_live,
)
from server.services.mock_data import MOCK_DSS

log = logging.getLogger(__name__)

DSS_APPS_SCRIPT_URL = os.getenv("DSS_APPS_SCRIPT_URL", "")

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
        if any(k in hl for k in ("situation", "scenario", "issue", "case")):
            col.setdefault("situation", i)
        if any(k in hl for k in ("action", "co action", "ce action", "response", "resolution")):
            col.setdefault("action", i)
        if any(k in hl for k in ("comp", "hoc", "refund", "credit")):
            col.setdefault("compensation", i)
        if "policy" in hl:
            col.setdefault("policy", i)
        if any(k in hl for k in ("team", "owner", "route")):
            col.setdefault("team", i)
    return col


def _is_escalation(team_val: str) -> bool:
    return "escalat" in team_val.lower()


def _is_ce_ro(team_val: str) -> bool:
    v = team_val.lower().strip()
    return not v or v in {"ce", "ro", "ce/ro"}


# ─── fetch ──────────────────────────────────────────────────────────────────

async def _fetch_via_apps_script() -> list[dict] | None:
    if not DSS_APPS_SCRIPT_URL:
        return None
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
            r = await c.get(DSS_APPS_SCRIPT_URL)
        if r.status_code != 200:
            return None
        ct = r.headers.get("content-type", "")
        if "json" not in ct:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        log.info("[dss] using Apps Script URL for fetch")
        rows = []
        for item in data:
            team_val = str(item.get("team", item.get("owner", ""))).strip()
            if _is_escalation(team_val) or not _is_ce_ro(team_val):
                continue
            rows.append({
                "situation":    str(item.get("situation", item.get("case", ""))).strip(),
                "action":       str(item.get("action", item.get("resolution", ""))).strip(),
                "compensation": str(item.get("compensation", item.get("comp", ""))).strip(),
                "policy":       str(item.get("policy", "")).strip(),
                "raw_row":      item,
            })
        log.info(f"[dss] fetched {len(rows)} CE/RO rows via Apps Script")
        return rows
    except Exception as e:
        log.warning(f"[dss] Apps Script fetch failed: {e}")
        return None


async def _fetch_csv() -> list[dict]:
    gid = 0

    # Try to resolve the named tab via Sheets metadata if GOOGLE_API_KEY is set
    if GOOGLE_API_KEY:
        try:
            meta_url = (
                f"https://sheets.googleapis.com/v4/spreadsheets/{DSS_SHEET_ID}"
                f"?fields=sheets.properties(title,sheetId)&key={GOOGLE_API_KEY}"
            )
            async with httpx.AsyncClient(timeout=10.0) as c:
                r = await c.get(meta_url)
            if r.status_code == 200:
                for sh in r.json().get("sheets", []):
                    props = sh.get("properties", {})
                    if props.get("title", "").strip().lower() == DSS_SHEET_TAB.strip().lower():
                        gid = props.get("sheetId", 0)
                        break
        except Exception as e:
            log.warning(f"[dss] sheets metadata lookup failed: {e}")

    csv_url = (
        f"https://docs.google.com/spreadsheets/d/{DSS_SHEET_ID}"
        f"/export?format=csv&gid={gid}"
    )
    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
        r = await c.get(csv_url)
    if r.status_code not in (200,):
        raise RuntimeError(f"DSS CSV export returned HTTP {r.status_code}")

    reader = csv.reader(io.StringIO(r.text))
    raw = list(reader)
    if not raw:
        return []

    headers = [h.strip() for h in raw[0]]
    col = _detect_cols(headers)
    log.info(
        f"[dss] mapped columns: situation={col.get('situation', '?')}, "
        f"action={col.get('action', '?')}, compensation={col.get('compensation', '?')}, "
        f"tab=gid:{gid}"
    )

    rows: list[dict] = []
    for rw in raw[1:]:
        if not any(c.strip() for c in rw):
            continue
        max_idx = max(col.values(), default=0)
        if max_idx >= len(rw):
            continue

        team_val = rw[col["team"]].strip() if "team" in col else ""
        if _is_escalation(team_val):
            continue
        if not _is_ce_ro(team_val):
            continue

        def _get(key: str) -> str:
            idx = col.get(key)
            return rw[idx].strip() if idx is not None and idx < len(rw) else ""

        rows.append({
            "situation":    _get("situation"),
            "action":       _get("action"),
            "compensation": _get("compensation"),
            "policy":       _get("policy"),
            "raw_row":      rw,
        })

    log.info(f"[dss] fetched {len(rows)} CE/RO rows from sheet (gid={gid})")
    for i, sample in enumerate(raw[1:4], 1):
        safe = [c[:40] if len(c) > 40 else c for c in sample[:5]]
        log.debug(f"[dss] sample row {i}: {safe}")
    return rows


async def _get_rows() -> list[dict]:
    global _cache_rows, _cache_at
    if _cache_rows and (time.time() - _cache_at) < _TTL:
        return _cache_rows
    try:
        rows = await _fetch_via_apps_script()
        if rows is None:
            rows = await _fetch_csv()
        _cache_rows = rows
        _cache_at = time.time()
    except Exception as e:
        log.warning(f"[dss] sheet fetch failed: {e}; using stale cache or empty")
        if not _cache_rows:
            _cache_rows = []
    return _cache_rows


# ─── public API ─────────────────────────────────────────────────────────────

async def get_recommendation(
    booking: dict,
    review_id: str = "",
    l1: str = "",
    l2: str = "",
    review_text: str = "",
) -> dict:
    if not is_live("dss"):
        return MOCK_DSS.get(review_id or "", {})

    rows = await _get_rows()
    if not rows:
        log.warning("[dss] no rows available — returning empty recommendation")
        return {}

    review_kw = _keywords(review_text or "")
    best_row: dict | None = None
    best_score = 0

    for row in rows:
        sit_lower = row["situation"].lower()
        score = 0
        if l2 and l2.lower() in sit_lower:
            score += 3
        if l1 and l1.lower() in sit_lower:
            score += 2
        overlap = len(review_kw & _keywords(row["situation"]))
        score += overlap
        if score > best_score:
            best_score = score
            best_row = row

    if not best_row or best_score == 0:
        log.warning(f"[dss] no match for l1={l1!r} l2={l2!r} (review_id={review_id})")
        return {"match_score": 0}

    return {
        "policy":       best_row["policy"],
        "compensation": best_row["compensation"],
        "action":       best_row["action"],
        "coverage":     "CE/RO",
        "escalateTo":   "",
        "matched_row":  best_row["raw_row"],
        "match_score":  best_score,
    }
