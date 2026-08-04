"""
DSS — the "DSS All in One" decision sheet, replicating the Retool app's logic.

The DSS agents actually use is a Retool app reading Google Sheet
1aQDO-qsKjW5Yrm7_b_Pwmz5rWOza3II1vIwfJf28z0I, one tab per DSS type, each
row's `dss` column holding the recommendation text:

    cancelation         Cancelation_Reason x is_Partenered x
                        For_Social_Media x is_value_greater
    meetingPointIssue   scenarios x when_did_the_guest_reached_out
    supplyPartnerIssue  scenarios
    delay_fulfilment    delay_fulfilment_reason x is_value_greater

This module applies the app's own filters instead of keyword-scoring a flat
sheet. The inputs the app takes from a human or its booking query, we take
from the pipeline's booking dict:

    For_Social_Media  = "Yes" always - every case here is a public review.
    team              = CE/RO always - the Escalations variants (rows routed
                      "Escalations", and the "- ES" scenario duplicates) are
                      a different desk's playbook and never apply here.
    is_Partenered     from booking.isPartnered (fulfilling vendor).
    is_value_greater  from booking.amountUSD > 125 (the app reads
                      PRICE_PAYABLE_USD and forks on the same threshold).

An unknown filter input skips that filter rather than guessing a side - the
matched row then says which variant it is, so the reader can see what was
assumed. The one genuinely soft step is picking the row's selector (reason /
scenario) from L1/L2/review keywords - previously that scoring WAS the whole
lookup; now it only chooses among rows that already passed the app's filters.

Fallback: MOCK_DSS in mock mode; {"match_score": 0} plus the app's own
"No DSS available" message when nothing matches.
"""
import csv
import io
import json as _json_std
import os as _os
import logging
import re
import time
from urllib.parse import quote

import httpx

from server.config import DSS_SHEET_ID, GCP_SERVICE_ACCOUNT_JSON, is_live
from server.services.mock_data import MOCK_DSS

log = logging.getLogger(__name__)

_TTL = 15 * 60  # seconds

# Tab -> its selector column (normalised header names)
TABS = {
    "cancelation":        "cancelation_reason",
    "meetingPointIssue":  "scenarios",
    "supplyPartnerIssue": "scenarios",
    "delay_fulfilment":   "delay_fulfilment_reason",
}

NO_DSS_MESSAGE = "No DSS available, Please check with your lead/escalation team."

# Returned by _route_type when the sheet has no tab for this L2 at all.
NO_TAB = "__no_tab__"

_STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
              "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
              "not", "no", "do", "did", "has", "have", "had", "that", "this",
              "they", "them", "their", "which", "what", "when", "where", "who",
              "guest", "booking", "booked"}

_cache_tabs: dict[str, list[dict]] = {}
_cache_at: float = 0.0


# ─── helpers ────────────────────────────────────────────────────────────────

# ── the new unified view ────────────────────────────────────────────────────
# A point-in-time export of the DSS "New (Unified View)" sheet, imported by
# tools/import_dss.py. It is checked in so the new guidance is available
# without waiting on the live sheet, and so it reviews like any other content
# change.
#
# WHERE A SCENARIO APPEARS IN BOTH, THIS WINS. That is the instruction, and it
# is the only rule that makes carrying both safe: a scenario in both has to
# resolve to one answer, and keeping the older text would hand an agent the
# guidance this file exists to replace.
_UNIFIED_PATH = _os.path.join(_os.path.dirname(_os.path.dirname(
    _os.path.dirname(_os.path.abspath(__file__)))), "content", "dss_unified.json")


def _load_unified() -> dict:
    """{dss_type: [row, ...]} from the checked-in export, or {}.

    Never raises: a malformed or missing file must cost the new rows, not the
    whole lookup. It logs the difference, because a file that failed to parse
    and a file that was never there are not the same fact.
    """
    try:
        with open(_UNIFIED_PATH, encoding="utf-8") as fh:
            payload = _json_std.load(fh)
    except FileNotFoundError:
        log.info("[dss] no unified export checked in - live sheet only")
        return {}
    except Exception as e:
        log.warning(f"[dss] unified export could not be read ({e}) - live sheet only")
        return {}
    out: dict[str, list[dict]] = {}
    for tab in (payload.get("tabs") or {}).values():
        t = tab.get("type") or "other"
        for row in tab.get("rows") or []:
            selector = row.get("selector") or ""
            new = {
                "_unified": True,
                "_column": row.get("column") or "",
                "scenarios": selector,
                "dss": row.get("dss") or "",
            }
            # The scorer reads the selector out of the column THAT TAB uses -
            # `cancelation_reason` for cancellations, `scenarios` for meeting
            # point. The export has one shape for every tab, so a cancellation
            # row carrying its scenario under `scenarios` scored 0 against
            # every review while still superseding the live row it replaced:
            # guidance removed and nothing put back, reported as
            # "No DSS available" - the same sentence a tab with no coverage
            # gets. Store it under both names.
            new.setdefault(TABS.get(t, "scenarios"), selector)
            out.setdefault(t, []).append(new)
    n = sum(len(v) for v in out.values())
    log.info(f"[dss] unified export: {n} row(s) across {len(out)} type(s)")
    return out


_UNIFIED = _load_unified()


def _selector_key(text: str) -> str:
    """A scenario name reduced to what makes it the same scenario.

    "HO Error wrong meeting point" and "Ho Error wrong meeting point" are one
    scenario written twice — the two exports differ by exactly that. Comparing
    raw text would carry both and let the older one win half the time.
    """
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _keywords(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _norm_header(h: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (h or "").strip().lower()).strip("_")


def _yn(value) -> str | None:
    """Normalise a yes/no-ish value from either side of the comparison.
    Returns 'yes' / 'no' / None (unknown). The sheet's is_Partenered and our
    booking.isPartnered arrive in different spellings of the same fact."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v in ("yes", "true", "1", "y"):
        return "yes"
    if v in ("no", "false", "0", "n"):
        return "no"
    return None


# The SP tab carries each scenario twice - a CE/RO row and an Escalations
# variant whose selector ends in "- ES" (e.g. "Venue was closed - ES"). The
# app also routes on an RO_CE vs Escalations selector. Both spellings of the
# same fact: this row belongs to the Escalations desk, not to CE/RO.
_ES_SELECTOR_RE = re.compile(r"[-–—]\s*ES\s*\.?\s*$")


def _is_escalation_row(row: dict, selector_col: str) -> bool:
    for col, val in row.items():
        if "team" in col or col in ("ro_ce", "routing", "coverage", "desk"):
            if "escalat" in str(val).lower():
                return True
    return bool(_ES_SELECTOR_RE.search(row.get(selector_col, "")))


def _row_passes(row: dict, col: str, ours: str | None) -> bool:
    """App-parity hard filter: keep the row when it matches our value, when
    the sheet has no such column, or when our own input is unknown."""
    if ours is None or col not in row:
        return True
    theirs = _yn(row.get(col))
    return theirs is None or theirs == ours


# ─── fetch ──────────────────────────────────────────────────────────────────

def _rows_from_grid(grid: list[list[str]]) -> list[dict]:
    if not grid:
        return []
    headers = [_norm_header(h) for h in grid[0]]
    rows = []
    for rw in grid[1:]:
        if not any(str(c).strip() for c in rw):
            continue
        row = {headers[i]: str(rw[i]).strip()
               for i in range(min(len(headers), len(rw)))}
        # is_Partenered is the sheet's spelling; accept the correct one too
        if "is_partnered" in row and "is_partenered" not in row:
            row["is_partenered"] = row["is_partnered"]
        if row.get("dss"):
            rows.append(row)
    return rows


def _fetch_tab_as_service_account(tab: str) -> list[dict]:
    """Sheets API read as the BigQuery service account. The DSS sheet is
    private, so the public CSV export gets a login page; sharing the sheet
    with the service account's client_email (Viewer) makes this path work
    without opening the sheet to the world. Sync on purpose - called via
    asyncio.to_thread, and google-auth's token refresh is sync anyway."""
    import json as _json
    from google.oauth2 import service_account
    from google.auth.transport.requests import Request as _GARequest

    creds = service_account.Credentials.from_service_account_info(
        _json.loads(GCP_SERVICE_ACCOUNT_JSON),
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"])
    creds.refresh(_GARequest())
    url = (f"https://sheets.googleapis.com/v4/spreadsheets/{DSS_SHEET_ID}"
           f"/values/{quote(tab, safe='')}")
    r = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"},
                  timeout=15.0)
    if r.status_code != 200:
        raise RuntimeError(f"tab {tab!r}: Sheets API HTTP {r.status_code} "
                           f"({r.text[:120]})")
    return _rows_from_grid(r.json().get("values") or [])


async def _fetch_tab(client: httpx.AsyncClient, tab: str) -> list[dict]:
    # gviz CSV export takes the tab NAME, so no gid resolution is needed.
    # Works only when the sheet is link-viewable; a private sheet returns a
    # login page, and then the service-account path below takes over.
    url = (f"https://docs.google.com/spreadsheets/d/{DSS_SHEET_ID}"
           f"/gviz/tq?tqx=out:csv&sheet={quote(tab)}")
    r = await client.get(url)
    if r.status_code == 200 and "csv" in r.headers.get("content-type", "").lower():
        return _rows_from_grid(list(csv.reader(io.StringIO(r.text))))
    if GCP_SERVICE_ACCOUNT_JSON:
        import asyncio
        return await asyncio.to_thread(_fetch_tab_as_service_account, tab)
    raise RuntimeError(
        f"tab {tab!r}: HTTP {r.status_code}, "
        f"content-type {r.headers.get('content-type', '?')} - sheet is not "
        f"link-viewable and no GCP_SERVICE_ACCOUNT_JSON is set")


async def _get_tabs() -> dict[str, list[dict]]:
    global _cache_tabs, _cache_at
    if _cache_tabs and (time.time() - _cache_at) < _TTL:
        return _cache_tabs
    tabs: dict[str, list[dict]] = {}
    try:
        async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as c:
            for tab in TABS:
                try:
                    tabs[tab] = await _fetch_tab(c, tab)
                except Exception as e:
                    log.warning(f"[dss] fetch failed for {tab!r}: {e}")
                    tabs[tab] = []
        if any(tabs.values()):
            _cache_tabs = tabs
            _cache_at = time.time()
            log.info("[dss] fetched " + ", ".join(
                f"{t}:{len(rs)}" for t, rs in tabs.items()))
    except Exception as e:
        log.warning(f"[dss] sheet fetch failed: {e}; using stale cache or empty")
    return _cache_tabs or tabs


# ─── type routing ───────────────────────────────────────────────────────────

_MP_RE     = re.compile(r"meeting[ -]?point|pickup point|pick-?up location", re.I)
_CANCEL_RE = re.compile(r"cancel|reschedul|modif|wrong (date|time|pax|name|experience)"
                        r"|double.?book|book(ed)? (twice|more than once)|refund", re.I)
_DELAY_RE  = re.compile(r"(ticket|voucher).{0,40}(not|never|delay|late|missing)"
                        r"|(not|never) receiv|fulfil|no ticket", re.I)


# L2s the sheet has no tab for. A content or pricing complaint is not a
# fulfilment case, but the review text says "did not receive what was
# described" and the delay pattern matched it - so the lookup went to
# delay_fulfilment and reported "no match" instead of "no tab covers this".
_NO_TAB_L2_RE = re.compile(
    r"content|instruction|mislead|pricing|convenience fee|discount|coupon|"
    r"audio guide|app issue|website", re.I)


def _route_type(l1: str, l2: str, review_text: str) -> tuple[str, str]:
    """Pick the DSS tab the app's human user would have picked, and say why."""
    hay = f"{l2 or ''} {review_text or ''}"
    # Classification first: it is a decision already made deliberately, and it
    # outranks a keyword that happens to appear in the guest's prose.
    if _NO_TAB_L2_RE.search(l2 or ""):
        # A distinct marker, not "": an empty type means "no type matched, so
        # score across every tab", which is how a content complaint ended up
        # matching a cancellation row.
        return NO_TAB, f"L2 {l2!r} has no DSS tab - the sheet covers " \
                       f"cancellation, meeting point, supply partner and " \
                       f"delayed fulfilment"
    if _MP_RE.search(hay):
        return "meetingPointIssue", "meeting-point terms in L2/review"
    if (l1 or "").strip() in ("Supply Partner Issue", "Venue Related Issue"):
        return "supplyPartnerIssue", f"L1 = {l1}"
    if _DELAY_RE.search(hay):
        return "delay_fulfilment", "ticket-delivery/fulfilment terms in L2/review"
    if _CANCEL_RE.search(hay):
        return "cancelation", "cancellation/modification terms in L2/review"
    return "", "no type matched - scoring across all tabs"


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

    tabs = await _get_tabs()
    if not any(tabs.values()):
        log.warning("[dss] no rows available - returning empty recommendation")
        return {}

    bk = booking or {}
    is_partnered = _yn(bk.get("isPartnered"))
    amount = bk.get("amountUSD")
    value_greater = None if amount is None else ("yes" if float(amount) > 125 else "no")

    dss_type, type_reason = _route_type(l1, l2, review_text)
    if dss_type == NO_TAB:
        log.info(f"[dss] no tab for l2={l2!r} (review_id={review_id}): {type_reason}")
        return {"match_score": 0, "dss_type": "", "type_reason": type_reason,
                "out_of_scope": True, "fallback": NO_DSS_MESSAGE,
                "filters": {"for_social_media": "Yes",
                            "is_partnered": _yn((booking or {}).get("isPartnered")) or "unknown"}}
    candidates = ([(dss_type, r) for r in tabs.get(dss_type, [])] if dss_type
                  else [(t, r) for t, rs in tabs.items() for r in rs])
    # The new unified view goes in FRONT, and any live-sheet row for the same
    # scenario comes out. Not appended: two rows for one scenario would let
    # the scorer pick either, so the "new one wins" instruction would hold
    # about half the time and look like it held always.
    #
    # Matched on a normalised selector, because the same scenario is written
    # differently in different exports - "HO Error" against "Ho Error" is the
    # difference between replacing a row and silently keeping both.
    _new = [(t, r) for t, rs in _UNIFIED.items() for r in rs
            if not dss_type or t == dss_type]
    if _new:
        _superseded = {_selector_key(r.get("scenarios")) for _, r in _new}
        _superseded.discard("")
        _kept = [(t, r) for t, r in candidates
                 if _selector_key(r.get(TABS.get(t, "scenarios"), "")) not in _superseded]
        _dropped = len(candidates) - len(_kept)
        if _dropped:
            log.info(f"[dss] unified view supersedes {_dropped} live-sheet row(s)")
        candidates = _new + _kept

    # The app's hard filters. Two are constant for this pipeline:
    # For_Social_Media = Yes (public review) and team = CE/RO (never the
    # Escalations desk's variant of the same scenario).
    filtered = []
    for tab, row in candidates:
        if _is_escalation_row(row, TABS[tab]):
            continue
        if not _row_passes(row, "for_social_media", "yes"):
            continue
        if tab == "cancelation" and not _row_passes(row, "is_partenered", is_partnered):
            continue
        if tab in ("cancelation", "delay_fulfilment") \
                and not _row_passes(row, "is_value_greater", value_greater):
            continue
        filtered.append((tab, row))

    # Selector choice - the one soft step. Score the selector column (plus
    # before/after-visit wording for MP rows) against L2 + review keywords.
    review_kw = _keywords(f"{l2 or ''} {review_text or ''}")
    best, best_score = None, 0
    for tab, row in filtered:
        selector = row.get(TABS[tab], "")
        score = len(review_kw & _keywords(selector))
        if l2 and l2.lower() in selector.lower():
            score += 3
        if score > best_score:
            best, best_score = (tab, row), score

    filters_applied = {
        "for_social_media": "Yes",
        "is_partnered":     is_partnered or "unknown",
        "value_greater_125": value_greater or "unknown",
        "amount_usd":       amount,
    }

    if not best:
        # "No tab covers this L2" is a different answer from "the tab was
        # searched and nothing matched", and only the second is worth a
        # warning. The first is the sheet's scope, correctly reported.
        log.warning(f"[dss] no match: type={dss_type!r} l1={l1!r} "
                    f"l2={l2!r} (review_id={review_id})")
        return {"match_score": 0, "dss_type": dss_type,
                "type_reason": type_reason, "filters": filters_applied,
                "fallback": NO_DSS_MESSAGE}

    tab, row = best
    return {
        "action":           row.get("dss", ""),
        "policy":           "",
        "compensation":     "",
        "coverage":         "CE/RO",
        "dss_type":         tab,
        "type_reason":      type_reason,
        "matched_selector": row.get(TABS[tab], ""),
        "when":             row.get("when_did_the_guest_reached_out", ""),
        "filters":          filters_applied,
        "matched_row":      row,
        "match_score":      best_score,
    }
