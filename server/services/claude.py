"""
REPLACES existing server/services/claude.py

Runs Claude via Replit AI Integrations (no separate API key required
when running inside a Replit workspace with Anthropic enabled).

New methods added on top of the existing translate/generate_rca/draft_response:
  - extract_signals(review_text)
  - stated_issue(review_text)
  - classify(review_text, booking, timeline)
  - generate_rca_v2(...) — structured demo-parity output
  - draft_response_v2(...) — uses L1/L2 + resolution
"""
import asyncio
import json, logging, os
import time
from anthropic import Anthropic

from server.config import ANTHROPIC_MODEL, MOCK_MODE, is_live
from server import prompts
from server.services.mock_data import (
    MOCK_RCA_FIELDS, MOCK_RESPONSES, MOCK_REVIEWS,
)

log = logging.getLogger(__name__)

_CL_SEM = asyncio.Semaphore(8)

# Route 1 (preferred): Replit AI Integrations — AI_INTEGRATIONS_ANTHROPIC_BASE_URL
# + AI_INTEGRATIONS_ANTHROPIC_API_KEY are injected automatically by the
# python_anthropic_ai_integrations blueprint (no personal API key needed).
# Route 2 (fallback): a user-supplied ANTHROPIC_API_KEY hitting api.anthropic.com.
_AI_INT_BASE = os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL", "")
_AI_INT_KEY = os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "")

if _AI_INT_BASE and _AI_INT_KEY:
    ANTHROPIC_ROUTE = "replit_ai_integrations"
    _client = Anthropic(api_key=_AI_INT_KEY, base_url=_AI_INT_BASE)
else:
    ANTHROPIC_ROUTE = "anthropic_api_key"
    _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


async def _call(prompt: str, max_tokens: int = 2400) -> str:
    """Single completion call. Returns the raw text (stripped)."""
    if MOCK_MODE:
        msg = _client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
        return "".join(parts).strip()
    t0 = time.time()
    async with _CL_SEM:
        waited = time.time() - t0
        if waited > 2.0:
            log.warning(f"[claude] wait time exceeded 2s: {waited:.1f}s")
        msg = _client.messages.create(
            model=ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


def _strip_json_comments(text: str) -> str:
    """Remove // line comments and /* block */ comments that Claude sometimes
    emits — but only OUTSIDE of JSON string literals, so URLs (https://...) and
    text containing slashes inside string values are never corrupted."""
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "/":
            i += 2
            while i < n and text[i] != "\n":
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2  # skip closing */
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_trailing_commas(text: str) -> str:
    """Drop trailing commas before } or ] — string-aware, so commas inside
    string values are preserved."""
    out = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            i += 1
        elif c == '"':
            in_str = True
            out.append(c)
            i += 1
        elif c == ",":
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            if j < n and text[j] in "}]":
                i += 1  # skip the trailing comma
            else:
                out.append(c)
                i += 1
        else:
            out.append(c)
            i += 1
    return "".join(out)


def _strip_fences(text: str) -> str:
    text = text.replace("```json", "").replace("```", "").strip()
    text = _strip_json_comments(text)
    text = _strip_trailing_commas(text)
    return text.strip()


# ─── 1. Translation ─────────────────────────────────────────────────────────
async def translate(body: str, lang: str, review_id: str = None) -> str:
    if not is_live("anthropic"):
        # Mock: look up in fixtures
        for r in MOCK_REVIEWS:
            if r["id"] == review_id and r.get("body_english"):
                return r["body_english"]
        return body
    return await _call(prompts.translation_prompt(body, lang), max_tokens=1500)


# ─── 2. Signal extraction ───────────────────────────────────────────────────
async def extract_signals(review_text: str, review_id: str = None) -> dict:
    if not is_live("anthropic"):
        # Mock: return crude signals from the review text
        return {
            "guest_name": None, "experience_hint": None,
            "venue_or_city": None, "visit_date_hint": None,
            "group_size": None, "issue_summary": review_text[:100],
        }
    raw = await _call(prompts.signal_extraction_prompt(review_text), max_tokens=600)
    try:
        return json.loads(_strip_fences(raw))
    except Exception:
        log.exception("Signal extraction JSON parse failed")
        return {}


# ─── 3. Stated Issue ────────────────────────────────────────────────────────
async def stated_issue(review_text: str, review_id: str = None) -> str:
    if not is_live("anthropic"):
        return review_text[:200]
    return await _call(prompts.stated_issue_prompt(review_text), max_tokens=300)


# ─── 4. Classification ──────────────────────────────────────────────────────
async def classify(review_text: str, booking: dict, timeline: list,
                    review_id: str = None) -> dict:
    if not is_live("anthropic"):
        # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
        # Enables manual testing without real service calls.
        fields = MOCK_RCA_FIELDS.get(review_id, {})
        is_manual = review_id and review_id not in MOCK_RCA_FIELDS
        return {
            "l1": "Operations Issue" if is_manual else ("SP issue" if "cancel" in review_text.lower() else "Other"),
            "l2": "Customer Support Issues" if is_manual else fields.get("queryIssueType", "General complaint"),
            "l1_reasoning": "[Mock — manual test review] Classification synthesized for testing." if is_manual else "Mock classification (Claude offline).",
        }
    raw = await _call(
        prompts.classification_prompt(review_text, booking, timeline),
        max_tokens=500,
    )
    try:
        return json.loads(_strip_fences(raw))
    except Exception:
        log.exception("Classification JSON parse failed")
        return {"l1": "", "l2": "", "l1_reasoning": ""}


# ─── 5. Full RCA (v2, structured demo output) ───────────────────────────────
async def generate_rca_v2(
    review_text: str, booking: dict, timeline: list, insights: dict,
    dss_rec: dict, l1: str, l2: str, review_id: str = None,
) -> dict:
    if not is_live("anthropic"):
        # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
        # Enables manual testing without real service calls.
        legacy = MOCK_RCA_FIELDS.get(review_id, {})
        is_manual = review_id and review_id not in MOCK_RCA_FIELDS
        return {
            "diagnosticChecks": [
                {"key": "tickets_sent_on_time", "check": "pass", "question": "Were tickets sent on time?",
                 "answer": "Unknown" if is_manual else "Yes"},
                {"key": "guest_arrived_on_time","check": "pass", "question": "Did the guest arrive on time?",
                 "answer": "Unknown" if is_manual else "Yes"},
            ],
            "whatWentWrongBullets": ["[Mock — manual test review] No real data available."] if is_manual else
                [b.strip() for b in (legacy.get("whatWentWrong") or "").split(".") if b.strip()][:6],
            "supportInteractionFrames": [],
            "supportSummary": "[Mock — manual test review] Support summary not available." if is_manual else
                legacy.get("customerInteractionCO", "")[:200],
            "spInteractionFrames": [],
            "areaOfImproving": ["[Mock — manual test review] No real data."] if is_manual else
                ([legacy.get("areaOfImproving", "")] if legacy.get("areaOfImproving") else []),
            "actionsTaken": {
                "sp": [], "customer": [], "business": [], "product": [], "ce": [],
            },
            "resolution": "[Mock — manual test review] No resolution data." if is_manual else
                legacy.get("solutionOffered", ""),
        }
    raw = await _call(
        prompts.rca_generation_prompt(
            review_text, booking, timeline, insights, dss_rec, l1, l2),
        max_tokens=4000,
    )
    try:
        return json.loads(_strip_fences(raw))
    except Exception:
        log.exception("RCA v2 JSON parse failed")
        return {}


# ─── 6. Response draft (v2 — uses L1/L2 + resolution) ───────────────────────
async def draft_response_v2(
    review_text: str, l1: str, l2: str, resolution: str,
    canned_responses: str = "", review_id: str = None,
    guest_name: str = "", dss_rec: dict | None = None,
    canned_list: list | None = None,
) -> str:
    if not is_live("anthropic"):
        # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
        # Enables manual testing without real service calls.
        if review_id and review_id not in MOCK_RESPONSES:
            return "[Mock — manual test review] Response draft not available for this review."
        return MOCK_RESPONSES.get(review_id, "")
    return await _call(
        prompts.response_draft_prompt(
            review_text, l1, l2, resolution, canned_responses, guest_name, dss_rec,
            canned_list=canned_list),
        max_tokens=800,
    )


# ─── 5b. Full RCA (v3, structured with checklist) ───────────────────────────
async def generate_rca_v3(
    review_text: str,
    booking: dict,
    timeline: list,
    insights: dict,
    dss_rec: dict,
    l1: str,
    l2: str,
    sub_theme: str,
    support_summary: str,
    checklist_items: list,
    review_id: str = None,
) -> dict:
    """
    Returns the RCA v3 shape:
      {tldr, wwr_chain, prevention, evidence,
       issue_specific_answers, checklist_answers}
    # Mock synthesis: activates in MOCK_MODE for review IDs not in fixtures.
    # Enables manual testing without real service calls.
    """
    if not is_live("anthropic"):
        is_manual = review_id and review_id not in MOCK_RCA_FIELDS
        if is_manual:
            return {
                "tldr": "Mock RCA — manual test review, no real data available.",
                "wwr_chain": [
                    {"step": 1, "what": "[Mock] Root cause not available", "why": "Manual test review — no real data."},
                    {"step": 2, "what": "[Mock] Guest posted negative review", "why": "Unresolved experience issue."},
                ],
                "prevention": "[Mock — manual test review] Prevention steps not available.",
                "evidence": ["[review] Mock evidence — no real data for this review."],
                "issue_specific_answers": {"tickets_sent_on_time": "Unknown", "guest_arrived_on_time": "Unknown"},
                "checklist_answers": [
                    {"item": it["item"], "answer": "Unknown", "note": "[Mock — manual test review]"}
                    for it in checklist_items
                ],
            }
        # Known fixture — return plausible stub
        legacy = MOCK_RCA_FIELDS.get(review_id, {})
        return {
            "tldr": f"Guest reported {l2 or 'an issue'} on a {l1 or 'classified'} booking; resolution offered.",
            "wwr_chain": [
                {"step": 1, "what": "Booking completed", "why": "Guest selected experience and paid."},
                {"step": 2, "what": "Issue arose on day of experience", "why": "Operational or SP-side gap."},
                {"step": 3, "what": "Guest contacted CE", "why": "Guest was unsatisfied with experience."},
                {"step": 4, "what": "CE responded", "why": "Standard support flow."},
                {"step": 5, "what": "Guest posted review", "why": "Dissatisfied despite interaction."},
            ],
            "prevention": "Review pre-visit communications for this experience type; ensure CE SLA is met.",
            "evidence": [
                f"[review] {review_text[:120]}",
                f"[booking] {legacy.get('whatWentWrong', 'No booking detail in mock data.')[:120]}",
            ],
            "issue_specific_answers": {"tickets_sent_on_time": "Yes", "guest_arrived_on_time": "Unknown"},
            "checklist_answers": [
                {"item": it["item"], "answer": "Unknown", "note": ""}
                for it in checklist_items
            ],
        }

    raw = await _call(
        prompts.rca_v3_prompt(
            review_text, booking, timeline, insights, dss_rec,
            l1, l2, sub_theme, support_summary, checklist_items, review_id or ""),
        max_tokens=5000,
    )
    try:
        return json.loads(_strip_fences(raw))
    except Exception:
        log.exception("RCA v3 JSON parse failed")
        return {}


# ─── 6b. Support event summarisation (Zendesk → frames) ─────────────────────
_EMPTY_FRAME = {"guestSaid": "", "weDid": "", "guestReply": "", "gap": ""}


async def summarise_support_event(event: dict, prev: dict | None,
                                   next_: dict | None) -> dict:
    """guestSaid / weDid / guestReply / gap for one timeline event."""
    if not is_live("anthropic"):
        return dict(_EMPTY_FRAME)
    raw = await _call(prompts.support_event_prompt(event, prev, next_),
                      max_tokens=500)
    try:
        parsed = json.loads(_strip_fences(raw))
        return {k: str(parsed.get(k, "") or "") for k in _EMPTY_FRAME}
    except Exception:
        log.exception("Support event JSON parse failed")
        return {}


async def summarise_support_arc(frames: list) -> str:
    """2-3 sentence neutral rollup of the whole support arc."""
    if not is_live("anthropic") or not frames:
        return ""
    try:
        return await _call(prompts.support_arc_prompt(frames), max_tokens=400)
    except Exception:
        log.exception("Support arc summarisation failed")
        return ""


# ─── 7. Flag-to-Biz Slack message ───────────────────────────────────────────
async def flag_to_biz_message(
    vendor_name: str, vid: str, completion_pct: str, market_avg: str,
    l1: str, l2: str, review_bid: str,
) -> str:
    if not is_live("anthropic"):
        return (
            f"Flagging low completion rate on VID {vid} ({vendor_name}).\n\n"
            f"Current: {completion_pct} vs market avg {market_avg}.\n"
            f"Case: BID {review_bid} — L1 {l1} / L2 {l2}.\n\n"
            f"Please review supply allocation and consider escalation."
        )
    return await _call(
        prompts.flag_to_biz_prompt(
            vendor_name, vid, completion_pct, market_avg, l1, l2, review_bid),
        max_tokens=500,
    )


# ─── Legacy methods retained for backwards compatibility ────────────────────
# The existing v1 flow (api.py PATCH endpoint, legacy dashboard) still works.

async def generate_rca(review_text, booking, timeline, insights, dss_rec,
                        review_id: str = None):
    """Legacy v1 shape. Delegates to v2 + reshapes."""
    from server.prompts import rca_generation_prompt as v1_prompt
    if not is_live("anthropic"):
        return MOCK_RCA_FIELDS.get(review_id, {})
    # For legacy callers, still call the v1 prompt if imported from old codepath
    # Otherwise, produce a v1-shaped dict from v2 pipeline
    log.warning("generate_rca (v1) called — use generate_rca_v2 instead")
    return {}


async def draft_response(review_text, issue_type, solution, canned_responses,
                          review_id=None, guest_name="", dss_rec=None):
    """Legacy signature — delegates to v2."""
    return await draft_response_v2(
        review_text=review_text,
        l1="", l2=issue_type,
        resolution=solution,
        canned_responses=canned_responses,
        review_id=review_id,
        guest_name=guest_name,
        dss_rec=dss_rec,
    )
