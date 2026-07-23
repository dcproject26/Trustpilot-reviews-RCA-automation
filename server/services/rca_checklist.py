"""
RCA Checklist service.

Fetches a Google Sheet CSV on startup and every 15 minutes (TTL cache).
Filters rows by L1 / L2 to return the checklist items applicable to a
given review classification.

Sheet: RCA_CHECKLIST_SHEET_ID / RCA_CHECKLIST_GID (env-overridable).
CSV column detection is heuristic and case-insensitive — mapping is logged
on first successful fetch so CE can verify alignment.

MOCK_MODE: returns a hardcoded 5-item stub so the RCA prompt always runs.
"""
import asyncio, logging, time
from typing import Optional

import httpx

from server.config import MOCK_MODE, RCA_CHECKLIST_SHEET_ID, RCA_CHECKLIST_GID

log = logging.getLogger(__name__)

_CACHE: list[dict] = []
_CACHE_TS: float = 0.0
_TTL = 15 * 60          # 15 minutes
_FETCH_LOCK = asyncio.Lock()

_MOCK_ITEMS = [
    {"item": "Were tickets/vouchers sent to the guest before the experience date?",
     "guidance": "Check Zendesk timeline for ticket dispatch event within 24h of booking.",
     "l1": "", "l2": "", "owner": "CE"},
    {"item": "Did the guest contact CE before posting the review?",
     "guidance": "Look for any inbound guest email or chat in the timeline.",
     "l1": "", "l2": "", "owner": "CE"},
    {"item": "Was CE's first response within the SLA window (4h)?",
     "guidance": "Compare guest first contact timestamp vs CE reply timestamp.",
     "l1": "", "l2": "", "owner": "CE"},
    {"item": "Was a comp or refund offered?",
     "guidance": "Check timeline for any comp mention or refund tag in Zendesk.",
     "l1": "", "l2": "", "owner": "CE"},
    {"item": "Has the underlying SP/venue issue been flagged to the operations team?",
     "guidance": "Look for a Slack flag or internal Zendesk note to ops.",
     "l1": "", "l2": "", "owner": "ORM"},
]


def _detect_columns(headers: list[str]) -> dict:
    """Return {role: index} for the five semantic columns."""
    mapping: dict[str, int] = {}
    for i, h in enumerate(headers):
        hl = h.lower().strip()
        if "item" in hl or "question" in hl or "check" in hl or "criteria" in hl or "checkpoint" in hl:
            mapping.setdefault("item", i)
        elif "l1" in hl or ("category" in hl and "l2" not in hl) or "applies" in hl or "applicable" in hl:
            mapping.setdefault("l1", i)
        elif "l2" in hl or "subtheme" in hl or "sub-theme" in hl or "sub_theme" in hl or "theme" in hl:
            mapping.setdefault("l2", i)
        elif "guidance" in hl or "description" in hl or "notes" in hl or "detail" in hl or "how to" in hl:
            mapping.setdefault("guidance", i)
        elif "owner" in hl or "team" in hl or "who" in hl:
            mapping.setdefault("owner", i)
    return mapping


def _parse_csv(text: str) -> list[dict]:
    import csv, io
    reader = csv.reader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in rows[0]]
    mapping = _detect_columns(headers)

    log.info(
        "[checklist] mapped columns: item=%s, l1=%s, l2=%s, guidance=%s, owner=%s, rows=%d",
        headers[mapping["item"]] if "item" in mapping else "?",
        headers[mapping["l1"]] if "l1" in mapping else "?",
        headers[mapping["l2"]] if "l2" in mapping else "?",
        headers[mapping["guidance"]] if "guidance" in mapping else "?",
        headers[mapping["owner"]] if "owner" in mapping else "?",
        len(rows) - 1,
    )

    items = []
    for row in rows[1:]:
        if not any(c.strip() for c in row):
            continue
        def cell(role: str) -> str:
            idx = mapping.get(role)
            if idx is None or idx >= len(row):
                return ""
            return row[idx].strip()

        items.append({
            "item":     cell("item"),
            "guidance": cell("guidance"),
            "l1":       cell("l1"),
            "l2":       cell("l2"),
            "owner":    cell("owner"),
        })

    if items:
        log.info(
            "[checklist] first 3 parsed items: %s",
            [it["item"][:80] for it in items[:3]],
        )
    return items


async def _fetch_sheet() -> list[dict]:
    url = (
        f"https://docs.google.com/spreadsheets/d/{RCA_CHECKLIST_SHEET_ID}"
        f"/export?format=csv&gid={RCA_CHECKLIST_GID}"
    )
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        return _parse_csv(resp.text)


async def _refresh() -> None:
    global _CACHE, _CACHE_TS
    try:
        items = await _fetch_sheet()
        _CACHE = items
        _CACHE_TS = time.time()
        log.info("[checklist] refreshed — %d items loaded", len(items))
    except Exception:
        log.warning("[checklist] fetch failed — using cached list (%d items)", len(_CACHE))


async def get_checklist(l1: Optional[str], l2: Optional[str]) -> list[dict]:
    """
    Returns the checklist items applicable to this (L1, L2).
    Filter rules:
      - Row's L1 column blank OR matches l1 (case-insensitive)
      - Row's L2 column blank OR matches l2
      - "Applies-to-all" rows (L1 and L2 both blank) always included
    Shape: [{"item": str, "guidance": str, "l1": str, "l2": str, "owner": str}, ...]
    Preserves sheet order.
    # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
    # Enables manual testing without real service calls.
    """
    if MOCK_MODE:
        return _MOCK_ITEMS

    async with _FETCH_LOCK:
        if time.time() - _CACHE_TS > _TTL:
            await _refresh()

    if not _CACHE:
        return []

    l1_lower = (l1 or "").lower().strip()
    l2_lower = (l2 or "").lower().strip()

    result = []
    for it in _CACHE:
        row_l1 = it["l1"].lower().strip()
        row_l2 = it["l2"].lower().strip()
        applies_all = not row_l1 and not row_l2
        l1_match = not row_l1 or (l1_lower and row_l1 == l1_lower)
        l2_match = not row_l2 or (l2_lower and row_l2 == l2_lower)
        if applies_all or (l1_match and l2_match):
            result.append(it)

    return result


async def warm_cache() -> None:
    """Call once at startup to populate the cache early."""
    if not MOCK_MODE:
        await _refresh()
