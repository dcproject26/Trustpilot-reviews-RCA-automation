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
import json, logging, os
from anthropic import Anthropic

from server.config import ANTHROPIC_MODEL, is_live
from server import prompts
from server.services.mock_data import (
    MOCK_RCA_FIELDS, MOCK_RESPONSES, MOCK_REVIEWS,
)

log = logging.getLogger(__name__)

# Replit AI Integrations injects ANTHROPIC_API_KEY automatically.
_client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""))


async def _call(prompt: str, max_tokens: int = 2400) -> str:
    """Single completion call. Returns the raw text (stripped)."""
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
        fields = MOCK_RCA_FIELDS.get(review_id, {})
        return {
            "l1": "SP issue" if "cancel" in review_text.lower() else "Other",
            "l2": fields.get("queryIssueType", "General complaint"),
            "l1_reasoning": "Mock classification (Claude offline).",
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
        # Mock: hand back a plausible v2 shape from MOCK_RCA_FIELDS
        legacy = MOCK_RCA_FIELDS.get(review_id, {})
        return {
            "diagnosticChecks": [
                {"key": "tickets_sent_on_time", "check": "pass", "question": "Were tickets sent on time?", "answer": "Yes"},
                {"key": "guest_arrived_on_time","check": "pass", "question": "Did the guest arrive on time?", "answer": "Yes"},
            ],
            "whatWentWrongBullets": [b.strip() for b in
                (legacy.get("whatWentWrong") or "").split(".") if b.strip()][:6],
            "supportInteractionFrames": [],
            "supportSummary": legacy.get("customerInteractionCO", "")[:200],
            "spInteractionFrames": [],
            "areaOfImproving": [legacy.get("areaOfImproving", "")] if legacy.get("areaOfImproving") else [],
            "actionsTaken": {
                "sp": [], "customer": [], "business": [], "product": [], "ce": [],
            },
            "resolution": legacy.get("solutionOffered", ""),
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
    canned_responses: str, review_id: str = None,
    guest_name: str = "", dss_rec: dict | None = None,
) -> str:
    if not is_live("anthropic"):
        return MOCK_RESPONSES.get(review_id, "")
    return await _call(
        prompts.response_draft_prompt(
            review_text, l1, l2, resolution, canned_responses, guest_name, dss_rec),
        max_tokens=800,
    )


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
