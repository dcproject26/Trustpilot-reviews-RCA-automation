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


def _sel_col(tab: str) -> str:
    """The selector column THIS tab scores on. Defaults to 'scenarios' so a tab
    outside the fixed four - the unified export carries an 'other' tab - does
    not KeyError. It used to: once the checked-in export (which has 'other')
    merged in, get_recommendation raised on TABS['other'], the pipeline swallowed
    it, and DSS returned nothing for EVERY review. Ran-vs-not-run, on the tab
    key itself."""
    return TABS.get(tab, "scenarios")


# Returned by _route_type when the sheet has no tab for this L2 at all.
NO_TAB = "__no_tab__"

_STOPWORDS = {"the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
              "of", "with", "by", "from", "is", "was", "are", "were", "be", "been",
              "not", "no", "do", "did", "has", "have", "had", "that", "this",
              "they", "them", "their", "which", "what", "when", "where", "who",
              "guest", "booking", "booked"}

# Keyword-fallback confidence bar. When the AI selector cannot run, the
# deterministic scorer used to accept any positive score as a match. Because
# the cancelation tab is 63 of 117 rows in the export (54%, with A/B/C
# column triplicates of most selectors), a single common-word intersection
# ("issues", "cancel", "guide") landed the majority of outage reviews on a
# cancelation row at raw score 1 - a coincidence steering the RCA prompt.
#
# The bar is a PROPORTION, not an absolute floor: overlap / selector-kw-count.
# An absolute floor of 2 refused a correct terse-scenario match
# ("the guide was not at the meeting point" hitting "Guide not present at
# MP" - overlap {guide}, raw score 1, ratio 0.5). The proportion attacks
# the base-rate problem at its source: long cancelation selectors need a
# proportional share of their own words, so a lone-word coincidence scores
# worst exactly where the noise lived.
#
# The cut was measured against the real 117-row export by
# tools/measure_dss_ratio.py (18 coincidence-noise words, 6 real-match
# reviews with natural targets in the export). Coincidence max ratio: 0.333.
# Real-match min ratio: 0.500. Midpoint gap: 0.42. Re-run the script if the
# export grows or the token vocabulary shifts.
MIN_KEYWORD_RATIO = 0.42

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

    # Issue type is decided from the REVIEW by the AI selector below, NOT routed
    # from L2. Routing on L2 meant a wrong or missing L2 blanked DSS before we
    # ever read the review — the exact cascade seen on a mis-classified booking
    # (L2=None -> out_of_scope -> no DSS). _route_type still runs, but only for
    # the type_reason context/logging; it no longer gates anything, and the
    # candidate set is ALL rows across ALL tabs.
    _routed_type, type_reason = _route_type(l1, l2, review_text)
    candidates = [(t, r) for t, rs in tabs.items() for r in rs]
    # The new unified view goes in FRONT, and any live-sheet row for the same
    # scenario comes out. Not appended: two rows for one scenario would let
    # the selector pick either, so the "new one wins" instruction would hold
    # about half the time and look like it held always.
    #
    # Matched on a normalised selector, because the same scenario is written
    # differently in different exports - "HO Error" against "Ho Error" is the
    # difference between replacing a row and silently keeping both.
    _new = [(t, r) for t, rs in _UNIFIED.items() for r in rs]
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
        if _is_escalation_row(row, _sel_col(tab)):
            continue
        if not _row_passes(row, "for_social_media", "yes"):
            continue
        if tab == "cancelation" and not _row_passes(row, "is_partenered", is_partnered):
            continue
        # value > 125 is NO LONGER a hard gate (removed by request): booking
        # value is often missing, so gating on it silently excluded valid rows.
        # The value is passed to the selector as context instead, and a
        # value-dependent judgement is surfaced to the associate (value_note).
        filtered.append((tab, row))

    filters_applied = {
        "for_social_media": "Yes",
        "is_partnered":     is_partnered or "unknown",
        "amount_usd":       amount,
    }
    # Value is context for the associate now, not a gate.
    value_note = ("" if amount is None else
                  f"Booking value ${amount} USD — where a resolution depends on "
                  f"the value threshold, that judgement is yours to make.")

    def _keyword_best(rows):
        # The old deterministic selector, kept as the FALLBACK when the AI
        # selector cannot run (no model / model error). Keyword overlap of the
        # selector column against L2 + review text.
        #
        # Returns (best_pair, score_with_bonus, raw_overlap, sel_kw_len,
        # l2_hit). The extra fields are what the acceptance check below reads
        # for the ratio bar and the L2-substring override - the scorer itself
        # keeps the same math (intersection + 3 if L2 is a substring). Ties
        # break by first-seen, kept as today (a stable order the tabs above
        # rely on for the escalations-lose and non-social-loses guarantees).
        review_kw = _keywords(f"{l2 or ''} {review_text or ''}")
        b, bs = None, 0
        b_raw, b_selkw_len, b_l2_hit = 0, 0, False
        for tb, rw in rows:
            sel_text = rw.get(_sel_col(tb), "")
            sel_kw = _keywords(sel_text)
            raw = len(review_kw & sel_kw)
            l2_hit = bool(l2 and l2.lower() in sel_text.lower())
            sc = raw + (3 if l2_hit else 0)
            if sc > bs:
                b, bs = (tb, rw), sc
                b_raw, b_selkw_len, b_l2_hit = raw, len(sel_kw), l2_hit
        return b, bs, b_raw, b_selkw_len, b_l2_hit

    # Selector: AI reads the review and picks the scenario that MEANS the same
    # thing, over all filtered rows (issue type is decided here, not from L2).
    # Keyword match is the fallback on a model outage so a transient failure
    # degrades to the old behaviour, never to a false "no DSS available".
    best, best_score, selector, sel_reason = None, 0, "ai", ""
    kw_raw, kw_selkw_len, kw_l2_hit, kw_ratio = 0, 0, False, 0.0
    kw_matched_selector = ""
    if filtered:
        # The selector picks by SCENARIO meaning; the full prescription can be a
        # long multi-step block and there can be >100 candidates (the unified
        # export alone is 117 rows), so an untruncated payload is an unbounded
        # prompt. Send the scenario in full and only a short hint of the action -
        # enough to disambiguate two similar scenario names - and attach the full
        # action locally from the chosen index after selection.
        cand_payload = [{"i": i, "scenario": rw.get(_sel_col(tb), ""),
                         "action": (rw.get("dss", "") or "")[:160]}
                        for i, (tb, rw) in enumerate(filtered)]
        try:
            from server.services import claude
            choice = await claude.select_dss_scenario(
                situation=(review_text or ""), candidates=cand_payload,
                value_usd=amount, is_partnered=is_partnered,
                experience=bk.get("experience"))
            sel_reason = choice.get("reason", "")
            idx = choice.get("index")
            if isinstance(idx, int) and 0 <= idx < len(filtered):
                best, best_score, selector = filtered[idx], 5, "ai"
            else:
                # AI ran and judged none of the scenarios fits — a real
                # no-match, distinct from the model failure handled below.
                selector = "ai-none"
        except Exception as e:
            log.warning(f"[dss] AI selection unavailable "
                        f"({type(e).__name__}: {e}); using keyword fallback "
                        f"(review_id={review_id})")
            selector = "keyword-fallback"
            best, best_score, kw_raw, kw_selkw_len, kw_l2_hit = \
                _keyword_best(filtered)
            # Confidence bar for the keyword scorer: raw overlap over the
            # winning selector's own keyword count. An absolute floor of 2
            # would have refused the terse-scenario match that
            # test_dss_unified.py pins; the ratio keeps it (overlap 1 / len 2
            # = 0.5, above the 0.42 cut) while pushing the long-cancelation
            # single-word coincidences (ratio 0.11-0.33) below the line.
            # An L2-substring hit overrides the ratio - the classifier
            # explicitly naming the L2 is a stronger signal than any overlap
            # count. The cut was measured; see MIN_KEYWORD_RATIO above.
            if best is not None:
                kw_ratio = (kw_raw / kw_selkw_len) if kw_selkw_len else 0.0
                tb, rw = best
                kw_matched_selector = rw.get(_sel_col(tb), "")
                accept = kw_raw >= 1 and (kw_l2_hit or kw_ratio >= MIN_KEYWORD_RATIO)
                if not accept:
                    # NAMED, NOT SILENT (rule 1). "The keyword scorer ran and
                    # nothing scored above the confidence bar" is a different
                    # fact from "the scorer never ran" and from "no row
                    # scored above zero". The below-threshold marker is what
                    # lets dss_entry and any future diagnostic tell them
                    # apart. Keep the ratio and overlap in the response so
                    # the trail line can name them.
                    log.info(f"[dss] keyword-fallback below threshold: "
                             f"overlap={kw_raw} sel_kw_len={kw_selkw_len} "
                             f"ratio={kw_ratio:.3f} < {MIN_KEYWORD_RATIO} "
                             f"(selector={kw_matched_selector!r}, "
                             f"review_id={review_id})")
                    best, best_score = None, 0
                    selector = "keyword-below-threshold"

    if not best:
        log.info(f"[dss] no scenario matched (selector={selector}) "
                 f"l1={l1!r} l2={l2!r} (review_id={review_id})")
        out = {"match_score": 0, "dss_type": "", "type_reason": type_reason,
               "filters": filters_applied, "selector": selector,
               "selector_reason": sel_reason, "value_note": value_note,
               "fallback": NO_DSS_MESSAGE}
        if selector == "keyword-below-threshold":
            # Rule 1 extras: the specific selector that almost-won, and the
            # exact ratio it hit. dss_entry reads these to write a trail
            # line that names what happened, and mutation_tests can pin
            # both the marker and the numbers.
            out["keyword_overlap"] = kw_raw
            out["keyword_selector_len"] = kw_selkw_len
            out["keyword_ratio"] = round(kw_ratio, 3)
            out["matched_selector_below_threshold"] = kw_matched_selector
        return out

    tab, row = best
    return {
        "action":           row.get("dss", ""),
        "policy":           "",
        "compensation":     "",
        "coverage":         "CE/RO",
        "dss_type":         tab,
        "type_reason":      type_reason,
        "matched_selector": row.get(_sel_col(tab), ""),
        "when":             row.get("when_did_the_guest_reached_out", ""),
        "filters":          filters_applied,
        "matched_row":      row,
        "match_score":      best_score,
        "selector":         selector,
        "selector_reason":  sel_reason,
        "value_note":       value_note,
    }
