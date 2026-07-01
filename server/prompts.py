"""
REPLACES existing server/prompts.py

Six prompts:
  1. translation_prompt      — translate review to English (unchanged)
  2. signal_extraction_prompt — extract name/experience/venue/date from review text
                                (for Tier 2 candidate matching)
  3. stated_issue_prompt      — 1-line summary of what the guest is complaining about
  4. classification_prompt    — L1 + L2 constrained to taxonomy
  5. rca_generation_prompt    — full structured RCA in the demo schema
  6. response_draft_prompt    — public Trustpilot reply (unchanged tone rules)
  7. flag_to_biz_prompt       — Slack message when completion rate is below market
"""
import json
from server.taxonomy import (
    L1_CATEGORIES, L2_OPTIONS, DIAGNOSTIC_CHECKS, GAP_TAXONOMY, SIGNAL_FIELDS,
)


# ─── 1. Translation (unchanged) ─────────────────────────────────────────────
def translation_prompt(body: str, lang: str) -> str:
    return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
Return ONLY the translation. No preamble, no label, no explanation.

Original ({lang}):
{body}"""


# ─── 2. Signal extraction (Tier 2 candidate matching) ─────────────────────────
def signal_extraction_prompt(review_text: str) -> str:
    return f"""Extract structured signals from this Trustpilot review so we can search BigQuery for the booking.

REVIEW:
{review_text}

Extract these fields. Use null if the review does not clearly mention that field. Do NOT invent.

Return ONLY a valid JSON object. No markdown, no preamble.

{{
  "guest_name":       "string or null (only if explicitly named)",
  "experience_hint":  "string or null (e.g. 'Vatican Museums', 'Eiffel summit')",
  "venue_or_city":    "string or null",
  "visit_date_hint":  "string or null (any date phrase, may be relative like 'today')",
  "group_size":       "integer or null (number of guests if stated)",
  "issue_summary":    "one short sentence describing the guest's complaint"
}}"""


# ─── 3. Stated Issue (1-line summary) ───────────────────────────────────────
def stated_issue_prompt(review_text: str) -> str:
    return f"""Summarise this Trustpilot review in 1-2 sentences. State what the guest is complaining about.
Neutral tone. Facts only. Do not adopt or defend the guest's framing.

REVIEW:
{review_text}

Return ONLY the summary text. No label, no preamble."""


# ─── 4. Classification (L1 + L2) ────────────────────────────────────────────
def classification_prompt(review_text: str, booking: dict, timeline: list) -> str:
    l1_list = "\n".join(f"  - {l1}" for l1 in L1_CATEGORIES)
    l2_map = "\n".join(
        f"  {l1}: {', '.join(opts) if opts else '(none defined)'}"
        for l1, opts in L2_OPTIONS.items()
    )

    return f"""Classify this case into the CX taxonomy below. Use ONLY the categories listed. Do not invent.

REVIEW (English):
{review_text}

BOOKING:
{json.dumps(booking or {}, indent=2)}

TIMELINE (chronological events from Zendesk):
{json.dumps(timeline or [], indent=2)}

AVAILABLE L1 CATEGORIES:
{l1_list}

AVAILABLE L2 SUB-CATEGORIES:
{l2_map}

Return ONLY a valid JSON object. No markdown.

{{
  "l1":           "exact L1 from the list above",
  "l2":           "exact L2 from the list under that L1",
  "l1_reasoning": "1-2 sentence justification, citing evidence from the timeline or review"
}}"""


# ─── 5. Full RCA generation (structured demo-parity output) ─────────────────
def rca_generation_prompt(
    review_text: str,
    booking: dict,
    timeline: list,
    insights: dict,
    dss_rec: dict,
    l1: str,
    l2: str,
) -> str:
    """
    Produces the demo's structured RCA output. Each field is a placeholder
    that the demo dashboard renders directly.
    """
    checks_for_l1 = DIAGNOSTIC_CHECKS.get(l1, [])
    checks_json   = json.dumps([
        {"key": c["key"], "question": c["question"]} for c in checks_for_l1
    ], indent=2)

    gap_list = "\n".join(f"  - {g}" for g in GAP_TAXONOMY)

    guest_events = [t for t in (timeline or []) if t.get("actor") == "guest"]
    co_events    = [t for t in (timeline or []) if t.get("actor") in ("co", "system")]
    sp_events    = [t for t in (timeline or []) if t.get("actor") == "sp"]

    return f"""You are writing a Root Cause Analysis (RCA) for a Trustpilot review at Headout.
Headout is a booking intermediary that sells tours and tickets operated by supply partners (SPs).

Your output will be rendered directly on an internal RCA dashboard for a CX associate to review.
Every field must be based ONLY on the evidence below. Do NOT invent times, names, amounts, or events.

=== REVIEW (English) ===
{review_text}

=== BOOKING ===
{json.dumps(booking or {}, indent=2)}

=== FULL TIMELINE ===
{json.dumps(timeline or [], indent=2)}

=== GUEST EVENTS ONLY ===
{json.dumps(guest_events, indent=2)}

=== CO/SUPPORT EVENTS ONLY ===
{json.dumps(co_events, indent=2)}

=== SP EVENTS ONLY ===
{json.dumps(sp_events, indent=2)}

=== INSIGHTS (from BigQuery) ===
{json.dumps(insights or {}, indent=2)}

=== DSS POLICY RECOMMENDATION ===
{json.dumps(dss_rec or {}, indent=2)}

=== CASE CLASSIFICATION ===
L1: {l1}
L2: {l2}

=== DIAGNOSTIC CHECKS TO RUN (based on L1) ===
For each check below, answer strictly Yes / No / Unknown. Do NOT elaborate — the associate reviews.
{checks_json}

=== ALLOWED GAP LABELS (for support interaction gap tags) ===
{gap_list}

---

RULES — follow exactly:

1. Only use facts from the data above. Do NOT invent timestamps, comp amounts, handle names,
   ticket numbers, or people's names beyond what appears in the source data.

2. For diagnostic checks: return one row per check listed above. Answer is Yes/No/Unknown ONLY —
   short justification if Unknown or No, one clause only.

3. For "whatWentWrong": bullet list. Each bullet is a fact from the timeline. No adjectives,
   no judgement, no invented resolution. Do NOT restate what the CX system is doing about it —
   that goes in areaOfImproving. Do NOT include wider-pattern insights — those live in the
   Insights section on the dashboard.

4. For "supportInteractionFrames": one frame per distinct back-and-forth thread
   (chat, email #1, email #2, phone). Chronologically ordered.
   Each frame:
     - type: "chat" | "email" | "call"
     - time: exact timestamp from timeline
     - label: short human-readable title, e.g. "Email #1 — handled by Minded AI"
     - guest_said: one sentence, the guest's message summarised
     - we_did: one sentence, what we (CE/AI) did in response
     - guest_reply: one sentence, what the guest said back (or "—" if no reply)
     - gap: null OR one label from the allowed gap labels list above
   IMPORTANT: Do NOT put SP-side exchanges in this list. They go in spInteractionFrames.

5. For "spInteractionFrames": one frame per SP exchange (CE → SP, SP → CE).
   Each frame:
     - time: exact timestamp
     - label: "CE → SP (vendor name)" or "SP → CE (vendor name)"
     - summary: one sentence
     - comp: string if compensation was mentioned, else null

6. For "areaOfImproving": ONLY things WE need to raise going forward. Not what others already did.
   Not what Tanaz / Carlo / anyone else already actioned in Slack. Bullet list, 2-5 items.
   Each bullet starts with a verb: "Raise...", "Check...", "Flag..."

7. For "actionsTaken": five arrays, one per tab (sp, customer, business, product, ce).
   Only include actions we STILL NEED TO RAISE from this case forward. If the SP already refunded
   on this specific case, sp = []. If comp was already issued, customer = [].
   Each action:
     - with: short label of what to raise
     - handle: "[handle placeholder]" if unknown — do NOT invent handles
     - time: "[to be raised]" or actual time if already done
     - context: 1-2 sentence context tying the action back to this case
     - where: "slack.com/[channel]/[placeholder]" — do not invent thread IDs

8. For "resolution": one line. Just what comp was given, e.g. "Refund + 25% HOC" or
   "No comp issued — guest error". Do NOT elaborate with dates/processing steps beyond
   what's in the timeline.

9. For "supportSummary": 1-2 sentences summarising the overall support handling with gaps + resolution.
   Use <strong>...</strong> HTML tags to bold key phrases (the demo renders these).

Return ONLY a valid JSON object with these exact keys. No markdown, no fences, no preamble.

{{
  "diagnosticChecks": [
    {{"key": "...", "check": "pass"|"fail"|"warn", "question": "...", "answer": "Yes"|"No"|"Unknown"|"No — <short reason>"}}
  ],
  "whatWentWrongBullets": ["bullet 1", "bullet 2", "..."],
  "supportInteractionFrames": [
    {{"type": "email"|"chat"|"call", "time": "...", "label": "...", "guest_said": "...", "we_did": "...", "guest_reply": "...", "gap": "..." or null}}
  ],
  "supportSummary": "1-2 sentences with <strong>...</strong> tags.",
  "spInteractionFrames": [
    {{"time": "...", "label": "...", "summary": "...", "comp": "..." or null}}
  ],
  "areaOfImproving": ["bullet 1", "bullet 2", "..."],
  "actionsTaken": {{
    "sp":       [{{"with":"...","handle":"...","time":"...","context":"...","where":"..."}}],
    "customer": [...],
    "business": [...],
    "product":  [...],
    "ce":       [...]
  }},
  "resolution": "Short line — comp given or 'No comp'"
}}"""


# ─── 6. Response draft (public Trustpilot reply) ────────────────────────────
def response_draft_prompt(
    review_text: str,
    l1: str,
    l2: str,
    resolution: str,
    canned_responses: str,
    guest_name: str = "",
    dss_rec: dict | None = None,
) -> str:
    name_hint = f"The guest's name is {guest_name}." if guest_name else ""

    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.
This reply will appear publicly on Trustpilot. Write it accordingly — professional, warm, specific.

REVIEW:
{review_text}

CASE CLASSIFICATION:
L1: {l1}
L2: {l2}

WHAT WAS RESOLVED:
{resolution}

DSS RECOMMENDATION:
{json.dumps(dss_rec or {}, indent=2)}

{name_hint}

CANNED RESPONSE LIBRARY (tone and structure guide ONLY — not a template to fill in):
{canned_responses}

INSTRUCTIONS:
1. The canned response library is a TONE guide only. Do not copy phrasing.
2. Reference the guest's SPECIFIC complaint in their own terms.
3. The compensation mentioned must match the resolution string exactly.
   Do NOT invent amounts. If resolution says "No comp issued", do NOT mention comp.
4. Genuine, non-defensive acknowledgement.
5. Use the guest's name if known; otherwise open warmly without a placeholder.
   NEVER leave literal placeholders (<Name>, <first name>, {{date}}, <X%>) in the output.
6. 3-5 sentences. No bullets. No headings.
7. Return ONLY the reply text. Nothing else.
"""


# ─── 7. Flag-to-Biz Slack message draft ─────────────────────────────────────
def flag_to_biz_prompt(
    vendor_name: str,
    vid: str,
    completion_pct: str,
    market_avg: str,
    l1: str,
    l2: str,
    review_bid: str,
) -> str:
    return f"""Draft a short Slack message for the Biz / Supply team flagging a low completion rate on a VID.

CONTEXT:
Vendor: {vendor_name} (VID {vid})
Current completion rate: {completion_pct}
Market average: {market_avg}
Related review BID: {review_bid}
Case classification: L1={l1}, L2={l2}

INSTRUCTIONS:
- Direct, factual, no emoji or fluff
- 3-4 short paragraphs max
- Ask for supply allocation review + escalation team follow-up
- No made-up names or handles

Return ONLY the Slack message text."""
