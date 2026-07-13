"""
REPLACES server/prompts.py (v2 — Task #3).

Adds: full priority-order classification prompt that outputs L1 + L2 + sub_theme
in one call. Existing prompts (translation, stated_issue, rca_generation,
response_draft, flag_to_biz) unchanged; only classification_prompt is upgraded.
"""
import json
from server.taxonomy import (
    L1_PRIORITY_ORDER, L2_OPTIONS, OPERATIONS_L2_PRIORITY_ORDER,
    DIAGNOSTIC_CHECKS, GAP_TAXONOMY, SIGNAL_FIELDS, SUB_THEME_REGISTRY,
)


# ─── 1. Translation ─────────────────────────────────────────────────────────
def translation_prompt(body: str, lang: str) -> str:
    return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
Return ONLY the translation. No preamble, no label, no explanation.

Original ({lang}):
{body}"""


# ─── 2. Signal extraction ───────────────────────────────────────────────────
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


# ─── 3. Stated Issue ───────────────────────────────────────────────────────
def stated_issue_prompt(review_text: str) -> str:
    return f"""Summarise this Trustpilot review in 1-2 sentences. State what the guest is complaining about.
Neutral tone. Facts only. Do not adopt or defend the guest's framing.

REVIEW:
{review_text}

Return ONLY the summary text. No label, no preamble."""


# ─── 4. Classification — L1 + L2 + sub-theme in ONE call ───────────────────
def classification_prompt(review_text: str, booking: dict, timeline: list) -> str:
    """
    Single call outputs: l1, l2, sub_theme (nullable), review_summary, reasoning.

    The prompt embeds the FULL taxonomy + priority rules + sub-theme frameworks
    (only for L2s that have one). Validators in services/claude.py catch any
    output that violates the taxonomy and fall back cleanly.
    """
    # Build the sub-theme frameworks section — only include the L2s that have one
    sub_theme_sections = []
    seen_frameworks = set()
    for (l1, l2), fw in SUB_THEME_REGISTRY.items():
        # Deduplicate SP framework which applies to many L2s
        fw_id = id(fw)
        if fw_id in seen_frameworks:
            continue
        seen_frameworks.add(fw_id)

        applies_str = f"L1={l1}, L2={l2}"
        if fw.get("applies_to_l2"):
            applies_str = f"L1={l1}, any of L2: {', '.join(fw['applies_to_l2'])}"

        exclusion_kw = ", ".join(fw["exclusion"])
        st_lines = []
        for code, name, cues in fw["sub_themes"]:
            cue_str = "; ".join(cues) if cues else "catchall — anything clearly on-topic that doesn't fit A-E"
            st_lines.append(f"  {code}. {name} — cues: {cue_str}")

        tiebreak = fw.get("tiebreak_rule", "")
        tiebreak_line = f"\nTiebreak: {tiebreak}" if tiebreak else ""

        sub_theme_sections.append(f"""
--- Sub-theme framework for {applies_str} ---
STEP 1 (exclusion): If PRIMARY complaint is any of: {exclusion_kw}
  → sub_theme = "{fw['exclusion_label']}"
  (NOTE: only if primary complaint, not if mentioned in passing as consequence)
STEP 2 (in strict priority order, stop at first match):
{chr(10).join(st_lines)}{tiebreak_line}""")

    sub_theme_block = "\n".join(sub_theme_sections)

    # Build the L1 priority list
    l1_priority = " > ".join(L1_PRIORITY_ORDER)
    ops_priority = " → ".join(OPERATIONS_L2_PRIORITY_ORDER)

    l2_map = "\n".join(
        f"  {l1}: {', '.join(opts) if opts else '(none)'}"
        for l1, opts in L2_OPTIONS.items()
    )

    return f"""You are a review issue classifier for Headout (an experiences booking platform).

Your task: assign exactly ONE L1 + exactly ONE L2 + (when applicable) exactly ONE sub_theme.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIORITY RULE — READ THIS FIRST
Each review gets exactly ONE L1. If multiple L1s match, use the highest priority:
  {l1_priority}
Within Operations Issue, check L2s in this order (stop at first match):
  {ops_priority}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

REVIEW (translated to English if needed):
{review_text}

BOOKING (may be empty):
{json.dumps(booking or {}, indent=2)}

TIMELINE (may be empty):
{json.dumps(timeline or [], indent=2)}

AVAILABLE L1 CATEGORIES: {L1_PRIORITY_ORDER}

AVAILABLE L2 SUB-CATEGORIES:
{l2_map}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
KEY BOUNDARY RULES (apply during L1/L2 choice):
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

- Meeting Point: physical inability to connect with guide/pickup → Operations / Meeting Point Issues.
  Beats Content, Customer Support Issues, and SP Guide No Show when guest couldn't find/reach.
- Ticket Issues: any ticket delivery, validity, wrong-attraction, wrong-date/time, or QR failure
  caused by Headout's system → Operations / Ticket Issues.
- Guide No Show: guest confirms guide simply didn't appear (not that they couldn't find guide)
  → Supply Partner / Guide No Show.
- Skip-the-line/priority failures at venue → Venue Related / Venue Overcrowding (Venue), NOT content.
- Support unresponsive / refund denied / wrong info given → Operations / Customer Support Issues.
  Even if the ORIGINAL issue was External Factor (e.g. force majeure), if the guest's main
  complaint is that support failed to help, classify as Customer Support Issues.
- Positive review with low stars → External Factor / Rating Mismatch. Never leave L2 blank.
- Gibberish / raw URLs / profanity-only → External Factor / Gibberish / Profanity.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
SUB-THEME FRAMEWORKS
Only populate sub_theme if the chosen (L1, L2) has a framework below.
Otherwise sub_theme = null.

For each framework: apply Step 1 (exclusion) first. If exclusion applies, use its label.
Otherwise apply Step 2 in strict priority order and stop at the first match.

IMPORTANT on exclusions: exclusion applies only when the listed keywords describe the
PRIMARY complaint, not when they appear as consequence of the actual complaint.
Example: "guide didn't show up so we waited 30 minutes" — the primary complaint is
guide no-show, not the wait. Do NOT apply exclusion.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
{sub_theme_block}
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Return ONLY valid JSON, no preamble, no markdown fences:

{{
  "l1": "exact L1 from list above",
  "l2": "exact L2 from that L1's list above",
  "sub_theme": "exact code + name like 'A. Guide No Show', OR null if no framework applies",
  "review_summary": "max 15 words in English summarising the core complaint. If positive: 'Positive experience with no issues reported.'",
  "reasoning": "1-2 sentence justification citing evidence from the review or timeline"
}}"""


# ─── 5. Full RCA generation (unchanged from v1 delta) ──────────────────────
def rca_generation_prompt(
    review_text: str,
    booking: dict,
    timeline: list,
    insights: dict,
    dss_rec: dict,
    l1: str,
    l2: str,
) -> str:
    checks_for_l1 = DIAGNOSTIC_CHECKS.get(l1, [])
    checks_json   = json.dumps([
        {"key": c["key"], "question": c["question"]} for c in checks_for_l1
    ], indent=2)
    gap_list = "\n".join(f"  - {g}" for g in GAP_TAXONOMY)

    guest_events = [t for t in (timeline or []) if t.get("actor") == "guest"]
    co_events    = [t for t in (timeline or []) if t.get("actor") in ("co", "system")]
    sp_events    = [t for t in (timeline or []) if t.get("actor") == "sp"]

    return f"""You are writing a Root Cause Analysis (RCA) for a Trustpilot review at Headout.
Your output will be rendered directly on an internal RCA dashboard.
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

=== INSIGHTS ===
{json.dumps(insights or {}, indent=2)}

=== DSS ===
{json.dumps(dss_rec or {}, indent=2)}

=== CLASSIFICATION ===
L1: {l1}
L2: {l2}

=== DIAGNOSTIC CHECKS TO RUN ===
Answer strictly Yes / No / Unknown. Do NOT elaborate — the associate reviews.
{checks_json}

=== ALLOWED GAP LABELS ===
{gap_list}

---

RULES:

1. Only use facts from the data above. Do NOT invent timestamps, comp amounts, handle names,
   ticket numbers, or people's names beyond what appears in the source data.

2. Diagnostic checks: one row per check listed above. Answer Yes/No/Unknown only.
   Optional short justification (one clause) if Unknown or No.

3. whatWentWrong: bullet list of facts from timeline. No adjectives, no invented resolution,
   no wider-pattern insights (those live in the Insights section on the dashboard).

4. supportInteractionFrames: one frame per distinct chat/email/call thread, chronological.
   NOT SP-side exchanges — those go in spInteractionFrames.
   Fields: type, time, label, guest_said, we_did, guest_reply, gap (or null).

5. spInteractionFrames: one frame per SP exchange. Fields: time, label, summary, comp.

6. areaOfImproving: only things WE need to raise going forward. Not what others already did.
   Bullet list, verb-first. 2-5 items.

7. actionsTaken: five arrays (sp, customer, business, product, ce). Only things still to raise.
   If SP already refunded on this specific case, sp = []. If comp was already issued, customer = [].
   Do NOT invent handles — use "[handle placeholder]" if unknown.

8. resolution: one line. Just what comp was given, e.g. "Refund + 25% HOC" or "No comp — guest error".

9. supportSummary: 1-2 sentences with <strong>...</strong> tags on key phrases.

Return ONLY valid JSON, no markdown:

{{
  "diagnosticChecks": [{{"key":"...","check":"pass"|"fail"|"warn","question":"...","answer":"Yes"|"No"|"Unknown"|"No — <short>"}}],
  "whatWentWrongBullets": ["..."],
  "supportInteractionFrames": [{{"type":"email"|"chat"|"call","time":"...","label":"...","guest_said":"...","we_did":"...","guest_reply":"...","gap":"..." or null}}],
  "supportSummary": "1-2 sentences.",
  "spInteractionFrames": [{{"time":"...","label":"...","summary":"...","comp":"..." or null}}],
  "areaOfImproving": ["..."],
  "actionsTaken": {{"sp":[],"customer":[],"business":[],"product":[],"ce":[]}},
  "resolution": "one line"
}}"""


# ─── 6. Response draft (unchanged) ─────────────────────────────────────────
def response_draft_prompt(
    review_text: str, l1: str, l2: str, resolution: str,
    canned_responses: str, guest_name: str = "",
    dss_rec: dict | None = None,
) -> str:
    name_hint = f"The guest's name is {guest_name}." if guest_name else ""
    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.

REVIEW:
{review_text}

CLASSIFICATION: L1={l1}, L2={l2}
RESOLUTION: {resolution}
DSS: {json.dumps(dss_rec or {}, indent=2)}
{name_hint}

TONE GUIDE (do not copy, structure only):
{canned_responses}

INSTRUCTIONS:
1. Tone guide only. Do not copy phrasing.
2. Reference the guest's SPECIFIC complaint in their own terms.
3. Compensation mentioned must match the resolution string exactly. Do NOT invent amounts.
4. Non-defensive acknowledgement.
5. Use guest's name if known; otherwise open warmly. Never leave literal placeholders.
6. 3-5 sentences. No bullets. No headings.
7. Return ONLY the reply text."""


# ─── 7. Flag-to-Biz Slack message ──────────────────────────────────────────
def flag_to_biz_prompt(
    vendor_name: str, vid: str, completion_pct: str, market_avg: str,
    l1: str, l2: str, review_bid: str,
) -> str:
    return f"""Draft a short Slack message flagging low completion on a VID.

Vendor: {vendor_name} (VID {vid})
Current completion: {completion_pct}  | Market avg: {market_avg}
Related review BID: {review_bid}
Classification: L1={l1} / L2={l2}

INSTRUCTIONS:
- Direct, factual, no emoji
- 3-4 short paragraphs max
- Ask for supply allocation review + escalation team follow-up
- No made-up names or handles

Return ONLY the Slack message."""
