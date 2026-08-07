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

# Loop-local: a bare module-level Semaphore binds to the first loop that
# awaits it and raises from any later loop (see server/aio.py).
from server.aio import LoopLocalSemaphore
_CL_SEM = LoopLocalSemaphore(8)

# Route 1 (preferred): Replit AI Integrations — AI_INTEGRATIONS_ANTHROPIC_BASE_URL
# + AI_INTEGRATIONS_ANTHROPIC_API_KEY are injected automatically by the
# python_anthropic_ai_integrations blueprint (no personal API key needed).
# Route 2 (fallback): a user-supplied ANTHROPIC_API_KEY hitting api.anthropic.com.
_AI_INT_BASE = os.getenv("AI_INTEGRATIONS_ANTHROPIC_BASE_URL", "")
_AI_INT_KEY = os.getenv("AI_INTEGRATIONS_ANTHROPIC_API_KEY", "")

# How long one completion may take, and how many times the SDK may retry it.
#
# The SDK's own defaults are a 600s read timeout and two retries — up to half
# an hour of blocking in ONE call. That is what wedged a pipeline run at
# "Step 1 of 8 — matching booking" with every review queued behind it waiting,
# and no timeout above it can help, because until the call returns there is no
# await point at which a cancellation can be delivered.
#
# 180s is several times the slowest healthy completion here (an RCA generation
# runs 20-40s). One retry, not two, so the worst case is ~6 minutes rather than
# ~30 and stays inside the batch runner's twelve-minute budget for a whole run.
CALL_TIMEOUT_S = 180.0
CALL_MAX_RETRIES = 1

_client_kwargs = {"timeout": CALL_TIMEOUT_S, "max_retries": CALL_MAX_RETRIES}

if _AI_INT_BASE and _AI_INT_KEY:
    ANTHROPIC_ROUTE = "replit_ai_integrations"
    _client = Anthropic(api_key=_AI_INT_KEY, base_url=_AI_INT_BASE, **_client_kwargs)
else:
    ANTHROPIC_ROUTE = "anthropic_api_key"
    _client = Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY", ""), **_client_kwargs)


def _messages_create(prompt: str, max_tokens: int):
    """The blocking SDK call, isolated so it can be run off the event loop."""
    msg = _client.messages.create(
        model=ANTHROPIC_MODEL,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    parts = [b.text for b in msg.content if getattr(b, "type", None) == "text"]
    return "".join(parts).strip()


async def _call(prompt: str, max_tokens: int = 2400) -> str:
    """Single completion call. Returns the raw text (stripped).

    The SDK client is SYNCHRONOUS, so awaiting this used to block the whole
    event loop for the length of the HTTP request. Two things followed, both
    invisible: _CL_SEM capped a concurrency that could never happen (one call
    at a time, whatever the limit said), and no watchdog anywhere above could
    fire, because a cancellation can only be delivered at an await point and
    there was none between entering the call and leaving it. A run wedged in
    here could not be stopped and could not be told apart from a healthy one.

    asyncio.to_thread moves the blocking call off the loop, which makes both
    the semaphore and every timeout above it real.
    """
    if MOCK_MODE:
        return await asyncio.to_thread(_messages_create, prompt, max_tokens)
    t0 = time.time()
    async with _CL_SEM:
        waited = time.time() - t0
        if waited > 2.0:
            log.warning(f"[claude] wait time exceeded 2s: {waited:.1f}s")
        return await asyncio.to_thread(_messages_create, prompt, max_tokens)


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
    # Track BOTH container kinds. Closing only braces left every unclosed array
    # open, so a response cut inside a list - the common case, since the long
    # fields here are lists - stayed unparseable and the whole RCA was lost.
    stack: list[str] = []
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
            elif c in "{[":
                stack.append(c)
            elif c in "}]":
                if stack:
                    stack.pop()
                if not stack:
                    end = i
                    break
    if end != -1:
        try:
            return json.loads(_strip_trailing_commas(s[start:end + 1]))
        except Exception:
            pass

    # Truncated tail. Close what is open, and if that still will not parse,
    # walk backwards dropping the last partial element and try again: the cut
    # can land mid-key or mid-number, where no amount of closing helps.
    def _close(frag: str, open_stack: list[str], inside_string: bool) -> str:
        if inside_string:
            frag += '"'
        frag = _strip_trailing_commas(frag.rstrip().rstrip(","))
        for ch in reversed(open_stack):
            frag += "}" if ch == "{" else "]"
        return frag

    body = s[start:]
    attempt = _close(body, stack, in_str)
    try:
        return json.loads(attempt)
    except Exception:
        pass

    # Recompute the state at each earlier cut point rather than assuming it.
    for cut in range(len(body) - 1, max(len(body) - 20000, 0), -1):
        if body[cut] not in ",}]\"":
            continue
        head = body[:cut + 1]
        st: list[str] = []
        ins = es = False
        for c in head:
            if ins:
                if es:
                    es = False
                elif c == "\\":
                    es = True
                elif c == '"':
                    ins = False
            elif c == '"':
                ins = True
            elif c in "{[":
                st.append(c)
            elif c in "}]":
                if st:
                    st.pop()
        if ins or not st:
            continue
        try:
            return json.loads(_close(head, st, False))
        except Exception:
            continue
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


async def detect_language(text: str) -> str:
    """The language a review was written in, as a short name ("Spanish").

    `slack.parse_review` hard-codes `"language": "en"` on every ingested
    review and nothing ever updated it, so a review that was demonstrably
    translated on the way in still read as English. The card could then not
    offer the guest's-language box at all — it drew one English box and a
    "language not established" notice — and the reply risked going out in
    English to a guest who did not write in English.

    Returns "" when it cannot tell, which is NOT English: the caller leaves
    the column alone and the card keeps saying the language is unestablished.
    A wrong language here sends a reply in a language the guest does not read,
    so declining is the safe direction.
    """
    if not is_live("anthropic") or not (text or "").strip():
        return ""
    out = await _call(
        "Name the language this text is written in. Reply with the English "
        "name of the language and nothing else — no punctuation, no "
        "explanation. If you cannot tell, reply UNKNOWN.\n\n"
        + str(text)[:1500], max_tokens=12)
    out = (out or "").strip().strip(".").strip()
    if not out or out.upper() == "UNKNOWN" or len(out) > 30 or " " in out.strip():
        return ""
    return out


async def translate_to(text: str, lang: str, review_id: str = None) -> str:
    """English -> the guest's language, for the outgoing reply.

    The reverse of translate(). Kept separate because the instruction is
    different: this one must preserve the reply's tone and any booking
    reference verbatim, where the inbound translation only has to be
    faithful.
    """
    if not is_live("anthropic"):
        return text
    return await _call(prompts.reply_translation_prompt(text, lang), max_tokens=1500)


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
    canned_list: list | None = None, takedown_verdict: str = "",
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
                    canned_list=canned_list, takedown_verdict=takedown_verdict),
                max_tokens=800,
            )
        return ""
    return await _call(
        prompts.response_draft_prompt(
            review_text, l1, l2, resolution, canned_responses, guest_name, dss_rec,
            canned_list=canned_list, takedown_verdict=takedown_verdict),
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
    scenarios_routed: list = None,
    issue_questions: list = None,
    canned_list: list = None,
    support_frames: list = None,
) -> dict:
    """
    Returns the RCA v3 shape:
      {what_went_wrong (5 headings),
       booking_logs, flags (failures only), support_interaction,
       sp_interaction, issue_specific_answers, takedown}

    checklist: {"general": ..., "ce": [...], "ro": [...], "scenarios": {...}}
    timeline_raw: raw Zendesk ticket comment bodies.
    scenarios_routed: primary + overlay scenario names for the checklist run.

    Mock synthesis (Brief v7.1):
      - Known fixture IDs → return plausible stub.
      - Unknown (manually pasted) → fall through to the real prompt path
        so the model produces a genuine RCA with grounded checklist answers.
    """
    is_fixture = review_id and review_id in MOCK_RCA_FIELDS
    is_manual  = review_id and review_id not in MOCK_RCA_FIELDS

    if is_fixture and not is_live("anthropic"):
        # Known demo fixture — plausible static stub in the new shape
        return {
            "what_went_wrong": {
                "guest_issues": [{"issue": l2 or "reported issue",
                                  "claim_accuracy": "Unknown",
                                  "evidence": "[zendesk] mock fixture"}],
                "what_happened": {"root_causes": [{"issue": l2 or "issue",
                                                   "cause": "Operational or SP-side gap.",
                                                   "classification": "Operational + HO"}],
                                  "operational_failure": None, "sop_gap": None,
                                  "pattern": "one-off - mock data"},
                "sp_escalation": {"escalated": "N/A", "detail": "mock fixture"},
                "fixes": {"teams": ["CE"], "actions": ["Resolution offered."], "owner": None},
            },
            "booking_logs": [],
            "flags": [],
            "support_interaction": [],
            "sp_interaction": {"raised": "N/A", "records": []},
            "issue_specific_answers": {"tickets_sent_on_time": "Yes", "guest_arrived_on_time": "Unknown"},
            "takedown": {"verdict": "No"},
        }

    # Unknown review (manual test) OR live mode: run the real prompt
    # 6000 was sized for the old shape. The v3 answer carries five WWR
    # headings, booking logs, flags, both interaction blocks, SOP compliance,
    # the fixed question bank, area-of-improving and takedown - a
    # complex case runs past 6000 and the JSON comes back cut mid-string.
    raw = await _call(
        prompts.rca_v3_prompt(
            review_text, booking, timeline, insights, dss_rec,
            l1, l2, sub_theme, support_summary, checklist, review_id or "",
            timeline_raw=timeline_raw, ticket_facts=ticket_facts,
            scenarios_routed=scenarios_routed, issue_questions=issue_questions,
            canned_list=canned_list, support_frames=support_frames),
        max_tokens=16000,
    )
    # _extract_json_object, not json.loads: it tolerates a preamble, fences and
    # - the case that actually bit - a truncated tail, closing the open string
    # and braces so a long RCA degrades to a partial one instead of vanishing.
    # Strict parsing turned one over-long answer into an empty RCA panel with
    # no visible cause.
    parsed = _extract_json_object(raw)
    if isinstance(parsed, dict) and parsed:
        # Truncation means the STRICT parse failed and the repair rescued it.
        # The old check asked whether the raw text ended in "}" - which a
        # perfectly complete answer wrapped in ```json fences never does, so
        # every single RCA was reported as truncated and the warning stopped
        # meaning anything.
        try:
            json.loads(_strip_fences(raw))
        except Exception:
            log.warning(f"[rca_v3] answer was truncated ({len(raw)} chars) and "
                        f"repaired - recovered {sorted(parsed.keys())}")
        return parsed
    log.error(f"[rca_v3] could not parse the model answer ({len(raw)} chars). "
              f"First 300: {raw[:300]!r}")
    return {}


# ─── 6b. Support event summarisation (Zendesk → frames) ─────────────────────
_EMPTY_FRAME = {"guestSaid": "", "weDid": "", "guestReply": "", "gap": ""}

# The Booking-created / Review-posted bookends are injected frame markers, not
# messages from anyone. pipeline.py frames every timeline row, so these two were
# being fed to a prompt that asks "what did the guest say / what did we do",
# which is an invitation to attribute a bookend to a person and to invent a gap.
_BOOKEND_ACTORS = {"creation", "review"}


async def summarise_support_event(event: dict, prev: dict | None,
                                   next_: dict | None) -> dict:
    """guestSaid / weDid / guestReply / gap for one timeline event."""
    if not is_live("anthropic"):
        return dict(_EMPTY_FRAME)
    if str((event or {}).get("actor", "")).strip().lower() in _BOOKEND_ACTORS:
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
    # 3000 WAS NOT ENOUGH FOR THE TIMELINES THIS ACTUALLY GETS. The prompt
    # asks for one shaped entry per raw event, the fetch caps at 41 events
    # (20 + elision marker + 20), and an entry with idx_range, time, thread,
    # actor, label and summary runs 60-100 tokens. Forty of those is past
    # 3000, so the answer was cut mid-array — and `_safe_parse_events` needs a
    # CLOSED array, so it returned [] and the whole timeline fell back to raw
    # ticket bodies under category labels.
    #
    # The RCA call next door already sizes for its own shape and repairs a
    # truncated tail; this one did neither. Same figure as that call, for the
    # same reason: a long case must degrade, not vanish.
    return await _call(prompt, max_tokens=16000)


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
