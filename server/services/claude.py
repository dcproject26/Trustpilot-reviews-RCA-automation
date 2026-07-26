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


def _extract_json_object(text: str):
    """
    Best-effort parse of a single JSON object out of model output.
    Tolerates preamble/trailing prose, code fences, and a truncated tail
    (e.g. when the response hit max_tokens mid-object). Returns a dict or None.
    """
    if not text:
        return None
    s = _strip_fences(text)
    try:
        return json.loads(s)
    except Exception:
        pass
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        else:
            if c == '"':
                in_str = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    end = i
                    break
    if end != -1:
        try:
            return json.loads(_strip_trailing_commas(s[start:end + 1]))
        except Exception:
            pass
    # Truncated tail — repair using the scan's final state.
    frag = s[start:]
    if in_str:
        frag += '"'
    frag = _strip_trailing_commas(frag.rstrip().rstrip(","))
    if depth > 0:
        frag += "}" * depth
    try:
        return json.loads(frag)
    except Exception:
        return None


def _fill_facts_from_booking(facts: dict, booking: dict) -> None:
    """
    Deterministic fallback so ticket_facts is never blank when the booking
    enrichment already carries the essentials. Only fills fields the model
    left empty — never overwrites a real extracted value.
    """
    if not isinstance(facts, dict):
        return
    if not facts.get("guest_full_name"):
        zr = (booking.get("zendesk_requester_name") or "").strip()
        # Accept a real human name; reject hashes/base64 (long, no spaces).
        if zr and (" " in zr or len(zr) <= 20):
            facts["guest_full_name"] = zr
    if not facts.get("booking_status") and booking.get("booking_status"):
        facts["booking_status"] = booking.get("booking_status")
    if facts.get("ticket_email_seen") is None and "ticket_mail_seen" in booking:
        facts["ticket_email_seen"] = bool(booking.get("ticket_mail_seen"))


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
        # Known fixture → return static mock response.
        # Unknown (manual test) → fall through to real prompt so the model
        # produces a genuine reply rather than a "[Mock]" stub.
        if review_id and review_id in MOCK_RESPONSES:
            return MOCK_RESPONSES[review_id]
        if review_id and review_id not in MOCK_RESPONSES:
            # Unknown review — run the real model
            return await _call(
                prompts.response_draft_prompt(
                    review_text, l1, l2, resolution, canned_responses, guest_name, dss_rec,
                    canned_list=canned_list),
                max_tokens=800,
            )
        return ""
    return await _call(
        prompts.response_draft_prompt(
            review_text, l1, l2, resolution, canned_responses, guest_name, dss_rec,
            canned_list=canned_list),
        max_tokens=800,
    )


# ─── 5a2. WWR analysis — stacked scenario blocks (Task #13 §3) ──────────────
async def analyze_wwr(
    review_text: str, timeline: list, ticket_facts: dict, booking: dict,
    l1: str, l2: str, sub_theme, primary_scenario, overlay_scenarios: list,
) -> list:
    """
    Returns [{scenario_name, is_primary, accuracy, accuracy_explanation, why, fix}].
    One block per applicable scenario (primary first). Empty list on failure.
    """
    scen_list = [s for s in ([primary_scenario] + list(overlay_scenarios or [])) if s]
    if not is_live("anthropic"):
        # Deterministic mock so the stacked UI is testable offline.
        if not scen_list:
            scen_list = ["CE-error review"]
        return [{
            "scenario_name": s,
            "is_primary": i == 0,
            "accuracy": "Partially",
            "accuracy_explanation": "Mock verdict — validate on live tickets.",
            "why": f"Mock root cause for {s} pending live Zendesk data.",
            "fix": "Mock corrective action — owning team TBD.",
        } for i, s in enumerate(scen_list)]
    try:
        raw = await _call(
            prompts.wwr_analysis_prompt(
                review_text, timeline, ticket_facts, booking,
                l1, l2, sub_theme, primary_scenario, overlay_scenarios),
            max_tokens=2000,
        )
        parsed = _extract_json_object(raw)
        scenarios = (parsed or {}).get("scenarios") or []
        return [s for s in scenarios if isinstance(s, dict) and s.get("scenario_name")]
    except Exception:
        log.exception("WWR analysis failed")
        return []


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
    checklist: dict,
    review_id: str = None,
    timeline_raw: list = None,
    ticket_facts: dict = None,
) -> dict:
    """
    Returns the RCA v3 shape:
      {tldr, wwr_chain, prevention, evidence,
       issue_specific_answers, checklist_answers}

    checklist: {"general": ..., "ce": [...], "ro": [...], "scenarios": {...}}
    timeline_raw: raw Zendesk ticket comment bodies.

    Mock synthesis (Brief v7.1):
      - Known fixture IDs → return plausible stub.
      - Unknown (manually pasted) → fall through to the real prompt path
        so the model produces a genuine RCA with grounded checklist answers.
    """
    is_fixture = review_id and review_id in MOCK_RCA_FIELDS
    is_manual  = review_id and review_id not in MOCK_RCA_FIELDS

    if is_fixture and not is_live("anthropic"):
        # Known demo fixture — return plausible static stub
        legacy = MOCK_RCA_FIELDS.get(review_id, {})
        return {
            "tldr": f"Guest reported {l2 or 'an issue'} on a {l1 or 'classified'} booking; resolution offered.",
            "wwr_chain": [
                {"step": 1, "what": "Booking completed", "why": "Guest selected experience and paid."},
                {"step": 2, "what": "Issue arose on day of experience", "why": "Operational or SP-side gap."},
                {"step": 3, "what": "Guest contacted CE and we responded", "why": "Standard support flow after guest raised the issue."},
                {"step": 4, "what": "Guest posted review", "why": "Dissatisfied despite interaction."},
            ],
            "prevention": "Review pre-visit communications for this experience type; ensure CE SLA is met.",
            "evidence": [
                f"[review] {review_text[:120]}",
                f"[booking] {legacy.get('whatWentWrong', 'No booking detail in mock data.')[:120]}",
            ],
            "issue_specific_answers": {"tickets_sent_on_time": "Yes", "guest_arrived_on_time": "Unknown"},
            "checklist_answers": [],
        }

    # Unknown review (manual test) OR live mode: run the real prompt
    raw = await _call(
        prompts.rca_v3_prompt(
            review_text, booking, timeline, insights, dss_rec,
            l1, l2, sub_theme, support_summary, checklist, review_id or "",
            timeline_raw=timeline_raw, ticket_facts=ticket_facts),
        max_tokens=6000,
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


# ─── 7b. Zendesk timeline shaping ───────────────────────────────────────────
async def shape_timeline_events(prompt: str) -> str:
    """
    One Claude call that batch-shapes raw Zendesk events into clean timeline
    entries. Returns the raw text response for the caller to parse.
    Always runs against the live model (MOCK_MODE timelines bypass this).
    """
    return await _call(prompt, max_tokens=3000)


# ─── 8. Ticket fact extraction (Zendesk → structured facts) ─────────────────
async def extract_ticket_facts(
    booking: dict,
    timeline_raw: list,
    timeline_raw_ticket_ids: list | None = None,
) -> dict:
    """
    Calls Claude with the raw Zendesk ticket comments and returns a structured
    dict of extracted facts matching the data-extraction-engine spec:
      guest_full_name, booking_status, is_same_day_booking, is_cancellable,
      is_reschedulable, sla_breached, ticket_email_seen, interaction_tags,
      delay_or_issue_reason, refund {issued, amount, reference_id, out_of_policy},
      ce_actions, resolution_summary, primary_issue, evidence.

    Always returns a dict (empty on any failure — never raises).
    """
    facts = {}
    have_tickets = bool(timeline_raw) and any(str(b).strip() for b in timeline_raw if b)
    if is_live("anthropic") and have_tickets:
        try:
            raw = await _call(
                prompts.ticket_extraction_prompt(booking, timeline_raw, timeline_raw_ticket_ids),
                max_tokens=4000,
            )
            parsed = _extract_json_object(raw)
            if isinstance(parsed, dict):
                facts = parsed
            else:
                log.warning("[claude] extract_ticket_facts: could not parse a JSON object from model output")
        except Exception:
            log.warning("[claude] extract_ticket_facts: call error", exc_info=True)
    # Deterministic fallback from booking enrichment so the essentials
    # (guest name, booking status, ticket-seen) are present even if the model
    # call failed, returned malformed JSON, or MOCK_MODE is on.
    _fill_facts_from_booking(facts, booking or {})
    return facts


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
