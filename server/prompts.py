"""
REPLACES server/prompts.py (v2 — Task #3).

Adds: full priority-order classification prompt that outputs L1 + L2 + sub_theme
in one call. Existing prompts (translation, stated_issue, rca_generation,
response_draft, flag_to_biz) unchanged; only classification_prompt is upgraded.

v3: classification_prompt's L1/L2 rules block replaced verbatim with the CX
ruleset (L1_L2_RULESET below). Sub-theme frameworks and the flat JSON output
shape are unchanged — the classifier and validators depend on that shape.
"""
import json
from server.taxonomy import (
    L1_PRIORITY_ORDER, L2_OPTIONS, OPERATIONS_L2_PRIORITY_ORDER,
    DIAGNOSTIC_CHECKS, GAP_TAXONOMY, SIGNAL_FIELDS, SUB_THEME_REGISTRY,
)


# ─── L1/L2 ruleset — verbatim from CX (do not edit by hand) ────────────────
L1_L2_RULESET = """━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
PRIORITY RULE — READ THIS FIRST
Each review gets exactly ONE L1. If multiple sections below match, use the highest priority:
  Operations Issue > Product Issue > Supply Partner Issue > Venue Related Issue > Business Issue > External Factor > Miscellaneous Issue
Within Operations Issue, check in this order: Meeting Point Issues → Ticket Issues → Content/Misleading Info → Customer Support Issues → Inventory Listing Issue
Apply this rule before reading the sections below.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
CLASSIFICATION RULES (read top-to-bottom, stop at first match)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

[OPERATIONS ISSUE — Headout's direct fault: meeting points, information, booking system, tickets]
→ L1 = "Operations Issue"
(Operations beats all other L1s if this section also matches)

*** CHECK MEETING POINT ISSUES FIRST — before any other Operations L2 ***

If the customer could not physically find, reach, or connect with the guide, driver, pickup point,
or tour start location — regardless of whether they also complained about support or unclear instructions:
  → L2 = "Meeting Point Issues"
  EXAMPLES:
  - "couldn't find the guide", "no one was at the meeting point", "guide was already inside, we were left outside"
  - "driver showed up at the wrong hotel", "pickup arrived at the wrong location", "driver took us to wrong address"
  - "transfer never turned up at the hotel", "waited 1.5 hours at the hotel pickup, nobody came"
  - "unable to locate the meeting point", "incorrect meeting point on the app", "wrong address on the voucher"
  - "we couldn't meet the guide after 30 minutes of waiting", "guide was inside the venue and we had no way to reach him"
  - "nobody came to pick us up", "the boarding point was wrong", "pickup point had changed and we weren't told"
  - "couldn't find where the tour started", "no one was at the specified location", "meeting point was different from what was shown"
  - "driver confirmed hotel then came to wrong hotel", "operator had the wrong pickup address"
  - "incorrect entry point given", "couldn't find the entrance to the experience", "wrong entry info on voucher"
  RULE: If the customer physically could not connect with the guide/pickup — TAG AS Meeting Point Issues.
  Even if they also say "instructions were unclear" or "support didn't help", Meeting Point Issues is the root cause — use it.
  BOUNDARY: guide definitely didn't show up (not just unfindable) → Supply Partner / Guide No Show
  BOUNDARY: customer did find the guide but the guide was bad → Supply Partner (Guide Behaviour Issues or Guide providing irrelevant/inexperienced/not clear)
  BOUNDARY: customer booked wrong date themselves → External Factor / Customer Error

If there was any ticket or booking failure — ticket not delivered, arrived late, invalid QR code,
wrong ticket sent, overbooking, wrong time slot sold, or customer received a ticket for the wrong
date/time/venue due to a system error:
  → L2 = "Ticket Issues"
  EXAMPLES: "received cathedral tickets instead of palace tickets", "tickets for the wrong attraction",
  "agency sent wrong tickets", "QR code never received", "ticket charged but never delivered",
  "ticket arrived 3 hours late", "sold a voucher not a ticket"
  BOUNDARY: wrong date/time/venue on ticket = Ticket Issues (system error), NOT Meeting Point Issues
  BOUNDARY: customer booked wrong date themselves = External Factor / Customer Error
  BOUNDARY: customer had a valid ticket and the experience ran — do NOT add this L2 just because they're unhappy
  NOTE: If the operator/agency physically sent the wrong tickets (e.g. wrong attraction) → Ticket Issues,
  NOT Content - Instructions not clear / Misleading Info.

If Headout's,email, voucher or website had missing or wrong information or what the experience entails:
  → L2 = "Content - Instructions not clear / Misleading Info"
  EXAMPLES:
  "misleading information present on website", "no information on tour details"
If the customer paid Headout for entry to an attraction that is actually free at the door (e.g. British
  Museum, some churches, some parks) and we have not mentioned on our website about free entry  → L2 = "Content - Instructions not clear / Misleading Info"




  → L2 =Customer expectation mismatch- 
 "I expected X based on the listing but got Y", "the description said X but it wasn't there",
  "tour was advertised as X but was actually Y", "we only visited one attraction but listing said multiple",
  "unclear description", "didn't know what was included", "no information on the website",
 (Headout sold something misleadingly — this is NOT External Factor / Sold Free Admission)
 
If a venue was closed on the day,  → L2 = "Venue closure"  NOTE: Do NOT use this for meeting point failures. If the customer couldn't find the guide/pickup → Meeting Point Issues.

BOUNDARY: If the customer complains about skip-the-line, priority access, fast track, or priority lines not working — still had to queue, lines were long despite booking priority — this is NOT a content issue.
  → Use Venue Related Issue / Venue facility issue (the venue failed to manage crowds/queues).
  BOUNDARY: Vague disappointment with no specific Headout failure — "not what I expected", "just Disneyland",
  "didn't enjoy it", "not interesting for adults" — is NOT a content issue.
  → Use Miscellaneous Issue / Vague review.

If Headout's support team failed to help the customer — unresponsive, denied a legitimate refund,
refused a reasonable reschedule request, or gave factually incorrect information:
  → L2 = "Customer Support Issues"
  EXAMPLES:
  - No response: "no one answered", "still waiting on a response", "tried to contact several times, no reply",
    "chat kept cutting off", "support@headout.com never replied", "90 minutes and nobody responded"
  - Refund denied: "stated would give refund but haven't", "charged 3 times, refuses to reimburse",
    "no refund despite cancellation", "still awaiting refund", "888€ unpaid for nothing"
  - Reschedule refused: "pregnant wife had a fever, tried to reschedule, got blocked",
    "flight changed, just wanted a date change, they didn't help",
    "husband had a heart attack, cancelled day before, refused with no consideration"
  - Wrong info given: "Headout support told us the wrong entry time", "false claims made by Headout's support team",
    "staff told us we could enter anytime before 17:30, which was false"
  BOUNDARY: if the underlying complaint is about the experience itself (guide was bad, tour was poor) and
    support is only mentioned in passing → classify the primary experience issue, not Customer Support Issues
  BOUNDARY: if support eventually resolved the issue → consider External Factor / Rating Mismatch

If the schedule on Headout's listing was wrong, or a ticket was listed that wasn't actually available:
  → L2 = "Inventory Listing Issue"
  EXAMPLES: "wrong schedule shown on Headout", "unavailability of tickets listed as available"

[PRODUCT ISSUE — Headout's tech layer: app, website, audio guide software]
→ L1 = "Product Issue"

If any audio guide was unavailable, didn't work, had wrong content, no language support, login failed,
couldn't download, was not provided as expected, or had poor quality — whether it's Headout's app,
a venue-provided handset, a hop-on hop-off bus audio system, or any other audio guide format:
  → L2 = "Audio Guide Issues"
  EXAMPLES: "audio guide not provided", "audio guide didn't work", "no audio guide at venue",
  "listing said audio guide included but we didn't get one", "audio device was broken",
  "couldn't download the audio guide", "audio guide was in the wrong language",
  "headset didn't work on the bus", "audio guide app crashed"

If Headout's mobile app or website didn't load or function:
  → L2 = "App and Website Issues"

[SUPPLY PARTNER ISSUE — Guide quality / Operator's fault]
→ L1 = "Supply Partner Issue"

If a tour guide never showed up at the meeting point and the customer confirms the guide was simply absent
(not that they couldn't find the guide):
  → L2 = "Guide No Show"
  BOUNDARY: customer couldn't locate the guide but guide may have been there → Operations / Meeting Point Issues

If a tour guide provided poor quality guiding — irrelevant information, inexperienced, unclear explanations,
wrong facts, couldn't answer questions, couldn't be heard, or rushed through the tour:
  → L2 = "Guide providing irrelevant/inexperienced/not clear"
  EXAMPLES: "guide gave wrong information", "guide was inexperienced", "guide couldn't explain clearly",
  "guide spoke too fast", "guide had bad English", "guide provided incorrect facts",
  "could not hear the guide", "guide couldn't answer our questions", "guide gave unnecessary information",
  "guide was in a hurry", "guide rushed through everything", "guide was unclear"
  NOTE: Only use when the experience involves a human tour guide — not for self-guided/app-based experiences.
  BOUNDARY: For performances, shows, musicals, or entertainment (sub_category: Musicals, Shows, Theatre,
  Performances) — audio/sound quality issues, unclear performers, poor acoustics → Venue Related Issue /
  Venue facility issue. These have no tour guide.

If a tour guide was rude, impolite, racist, unprofessional, or behaved inappropriately toward customers:
  → L2 = "Guide Behaviour Issues"
  EXAMPLES: "guide was rude", "guide was impolite", "guide was racist",
  "guide was aggressive", "guide made us feel unwelcome", "guide was dismissive",
  "guide was not paying attention", "guide was inattentive"
  BOUNDARY: For performances/shows/musicals — rude or unhelpful venue staff → Venue Related Issue / Venue facility issue.

If a tour guide abandoned, left, or disappeared before completing the tour:
  → L2 = "Guide Left / Abandoned Tour"
  EXAMPLES: "guide disappeared in the middle of the tour", "guide left us alone",
  "guide did not complete the tour", "guide walked off midway", "guide left early without finishing"

If tour started significantly late, ended early, or had unexpected timing changes made by the operator:
  → L2 = "Timing Issues"
  EXAMPLES: "guide cancelled last minute", "tour started 45 mins late", "tour ended early",
  "unexpected reschedule", "timing changed without notice", "guide was late"

If the venue or operator cancelled the tour/experience (not weather-related, not Headout's fault):
  → L2 = "Tour Cancelled by Operator"

If the physical seating experience was poor (bad view, cramped, uncomfortable, wrong seats):
  → L2 = "Seating Issues"

If food, catering, or meals provided as part of the experience were poor quality, insufficient, or not delivered:
  → L2 = "Food & Catering"
  NOTE: Food included in the package (cruise dinners, safari meals, tasting tours) is the supply partner's responsibility.
  EXAMPLES: "food was not served", "food was cold", "food was not tasty", "not given unlimited drinks as promised"

[VENUE RELATED ISSUE — Physical venue problems: facilities, conditions, overcrowding, closure]
→ L1 = "Venue Related Issue"

If the venue had poor facilities, dirty or broken conditions, poor navigation/signage,
or broken/malfunctioning equipment that the venue itself could have managed:
  → L2 = "Venue facility issue"
  EXAMPLES:
  - Poor conditions: "dirty pools", "broken equipment", "poor hygiene", "unclean spaces",
    "restroom not clean", "limited restrooms", "park was dirty", "drinking water facility not available",
    "broken audio/visual equipment"
  - Navigation/signage: "no signs", "maps not provided", "difficult to find way inside venue",
    "lack of information at venue", "misleading sign boards", "difficult to navigate",
    "no guidance inside the venue"
  BOUNDARY: if the complaint is about overcrowding, long queues, or crowd mismanagement
  → use "Venue Overcrowding (Venue)" instead.

If the venue was overcrowded, had long queues, or failed to manage crowds — including
skip-the-line / priority access / fast-track failures where the venue did not honour the
expedited entry process:
  → L2 = "Venue Overcrowding (Venue)"
  EXAMPLES:
  - Overcrowding/queues: "long queues", "long wait time", "overcrowding",
    "logistical issues at the venue", "too many people", "impossible to move around"
  - Skip-the-line/priority failures: "bought skip-the-line but still had to queue",
    "priority access didn't work", "fast track ticket was useless, still waited 2 hours",
    "priority line was just as long", "no difference between regular and priority queue",
    "no fast entry to St Peter's despite paying for it", "priority access was useless,
    same queue as everyone else", "fast track ticket didn't save any time",
    "despite having a timed ticket we waited 1 hour"
  NOTE: Skip-the-line/priority access complaints ALWAYS belong here — the venue failed to honour
  the fast-track process. This is NEVER a content/misleading issue on Headout's side.
  BOUNDARY: if overcrowding was due to external events completely beyond the venue's control
  (public holidays, cruise ships docking) → External Factor / Venue Overcrowding (External)

If the venue or attraction was closed on the day AND Headout proactively communicated this OR the closure was genuinely unforeseeable by Headout OR If Headout failed to warn the customer about a  closure:
  → L2 = "Venue closure"
 
 NOTE: → If the closure is happening for multiple days or ots a prolonged closure than its Operations Issue / Content - Instructions not clear / Misleading Info instead (Headout's communication failure). ALSO covers: ride or activity closure at theme parks, partial closures at zoos/parks.

[BUSINESS ISSUE — Pricing concerns]

→ L1 = "Business Issue"

If customer says Headout charges more than buying direct, at venue, or vs other platforms:
  → L2 = "Pricing Issues"

If customer felt overcharged, ripped off, or that the experience was not worth the price paid:
  → L2 = "Pricing Issues"
  NOTE: "Felt ripped off", "too expensive for what it was", "not worth the money" all qualify even
  without an explicit platform comparison.

[EXTERNAL FACTOR — Truly external, nobody's fault → AUTO-MODERATED: will be hidden from public]
→ L1 = "External Factor"

Use ONLY when neither Headout nor the supply partner could have prevented the issue.

If customer arrived late and missed the experience, AND the review explicitly states it was their own
fault or gives no other cause for the lateness:
  → L2 = "Customer Late"
  BOUNDARY: do NOT assign Customer Late if the review gives any other reason (wrong instructions, external
  event, transport failure beyond their control). When in doubt → Miscellaneous Issue / General negative exp.
  BOUNDARY: flight diversion, flight cancellation, travel ban, or any transport failure outside the
  customer's control is NOT Customer Late → use Force Majeure instead.

If the customer made a booking mistake, selected the wrong ticket, booked wrong dates, or was not
allowed entry due to dress code violation or failing to meet entry requirements (e.g. height restrictions,
clothing rules at religious sites):
  → L2 = "Customer Error"
  EXAMPLES: "I booked wrong dates by mistake", "not allowed due to clothes", "sleeveless clothes not permitted",
  "knee-length clothes not allowed at the site", "strict cancellation policy, my own mistake",
  "I accidentally booked wrong tickets", "could not amend tickets — booking mistake by guest",
  "chose wrong ticket type"
  BOUNDARY: if Headout's listing didn't mention the dress code or entry requirements → Operations / Content - Instructions not clear / Misleading Info

If rain, snow, wind, heat, river levels, or other weather/natural conditions ruined the experience:
  → L2 = "Weather Related"

If venue was overcrowded due to external events completely beyond the venue's control
(e.g., public holidays, cruise ships docking, unrelated external events):
  → L2 = "Venue Overcrowding (External)"
  BOUNDARY: if the venue had the capacity to manage crowds but didn't → Venue Related Issue / Venue facility issue

If an unavoidable force majeure event disrupted the experience (natural disaster, government restriction,
strike, flight cancellation/diversion, travel ban, war, pandemic restriction):
  → L2 = "Force Majeure"
  NOTE: Flight diversions, train cancellations, and flight cancellations are Force Majeure — not Customer Late.
  BOUNDARY: If the external event is valid FM BUT the customer's main complaint is that Headout refused to
  refund, kept the money, or never responded to their emails/messages → Operations / Customer Support Issues.
  The support failure is the actionable issue, not the external event.

If the customer explicitly states they received a complimentary or heavily discounted ticket from Headout
and is rating poorly despite acknowledging the deal:
  → L2 = "Sold Free / Discounted Admission"
  NOTE: This is NOT for customers who are angry they paid for an attraction that's free at the door —
  that case is Operations / Content - Instructions not clear / Misleading Info.

If the review text is genuinely positive with no real complaint
(customer gave low stars despite a good experience):
  → L2 = "Rating Mismatch"
  EXAMPLES: "quick and uncomplicated entry", "the experience was truly impressive",
  "everything went smoothly", "great day out", "would recommend" — positive language with a low star rating.
  NOTE: Always assign this L2 explicitly — do not leave L2 blank/null for rating mismatch cases.
  NOTE: Even if the review mentions one minor gripe alongside overall praise — if the dominant tone is
  positive, use Rating Mismatch. Do NOT assign Miscellaneous / General negative exp for positive reviews.

If the review is pure gibberish (random characters, keyboard mashing, test input, a URL or link with no
text, incomprehensible text) or contains profanity/abuse with no substantive complaint:
  → L2 = "Gibberish / Profanity"
  NOTE: This MUST be auto-moderated (External Factor). Raw URLs, app deep-links, and keyboard spam
  submitted as reviews qualify. Do not send these to Miscellaneous Issue.

[MISCELLANEOUS ISSUE — Negative review but no clear-cut L1 fit]
→ L1 = "Miscellaneous Issue"

This L1 has three L2 values. Pick the most specific one that fits.

L2 = "Vague review"
  Use when the review is negative in tone but states NO actionable reason — just generic
  dissatisfaction or a one-line dismissal with no detail about what went wrong.
  EXAMPLES: "not worth it", "disappointing", "nothing special", "boring", "won't recommend",
  "it was bad", "waste of time", "meh", "wouldn't do it again"
  BOUNDARY: any specific complaint (even one word like "queue", "guide", "rude") → use the
  matching L1/L2 instead. Vague review is ONLY for reviews with zero actionable detail.

L2 = "Negative Headout"
  Use ONLY when the review's dominant tone is negative toward Headout as a company —
  calling it a scam, fraud, ripoff, or warning others off — with no specific actionable
  complaint about Headout, the operator, or the venue.
  EXAMPLES: "this is a scam", "total fraud, don't buy", "ripoff, stay away",
  "scam company, avoid", "fraudulent service", "don't book with Headout"
  BOUNDARY: if the customer specifies what went wrong (no refund, wrong tickets, support
  never replied, etc.) — classify under the appropriate L1/L2 even if they also use the
  word "scam" or "fraud". This L2 is ONLY for emotional-accusation-with-no-specifics.

L2 = "General negative exp"
  Use when the review expresses dissatisfaction with some substance, but does not clearly
  fit any other L1 category. Includes personal-taste complaints and borderline cases
  between L1s where insufficient detail prevents a clear call.
  Use when:
  - Customer's dissatisfaction is purely subjective or a matter of personal taste
  - Complaint is borderline between External Factor and another L1 but not clearly either
  - Unclear if customer arrived late (their fault) vs Headout's info was wrong — insufficient detail to decide
  BOUNDARY: if the review is one-line or has zero actionable detail → use Vague review instead.

DO NOT use any Miscellaneous L2 for:
- Reviews that clearly belong to Operations, Supply Partner, Business, Product, or Venue Related Issue
- Reviews where the issue is obviously Headout's or the partner's fault → pick the correct L1 instead

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
TRANSLATION: Translate non-English reviews internally. Always output in English.

CRITICAL: L1 must ALWAYS be one of exactly these 7 strings:
  "Operations Issue", "Product Issue", "Supply Partner Issue", "Venue Related Issue",
  "Business Issue", "External Factor", "Miscellaneous Issue"
Never omit L1. Never invent new L1 values.
CRITICAL: L2 names are L2 values ONLY — NEVER use them as L1 values.
CRITICAL: L2 Issues must NEVER be an empty list. Every response must have at least one L2.
  If the review is positive → External Factor / Rating Mismatch.
CRITICAL: L2 Issues must contain EXACTLY ONE value — never more than one.
  L2 must ONLY come from the section matching your chosen L1.
  Do NOT add L2s from other sections even if the review mentions multiple issues.
  Pick the single L2 that best describes the primary problem within your chosen L1.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""


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

    The prompt embeds the FULL L1/L2 ruleset (L1_L2_RULESET, verbatim from CX)
    + sub-theme frameworks (only for L2s that have one). Validators in
    services/claude.py catch any output that violates the taxonomy and fall
    back cleanly.
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

    l2_map = "\n".join(
        f"  {l1}: {', '.join(opts) if opts else '(none)'}"
        for l1, opts in L2_OPTIONS.items()
    )

    return f"""You are a review issue classifier for Headout (an experiences booking platform).

Your task: assign exactly ONE L1 + exactly ONE L2 + (when applicable) exactly ONE sub_theme.

{L1_L2_RULESET}

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


# ─── 6. Response draft ──────────────────────────────────────────────────────
def response_draft_prompt(
    review_text: str, l1: str, l2: str, resolution: str,
    canned_responses: str = "", guest_name: str = "",
    dss_rec: dict | None = None,
    canned_list: list | None = None,
) -> str:
    name_hint = f"The guest's name is {guest_name}." if guest_name else ""

    # Tone examples — from live canned sheet (preferred) or legacy string block
    if canned_list:
        tone_block_lines = [
            "━━ TONE EXAMPLES (Headout's real past responses — use as tone reference, do NOT copy) ━━",
        ]
        for i, ex in enumerate(canned_list[:3], 1):
            tone_block_lines.append(f"Example {i} [situation: {ex['situation']}]:")
            tone_block_lines.append(ex["response"])
        tone_block_lines.append(
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )
        tone_block = "\n".join(tone_block_lines)
    elif canned_responses:
        tone_block = f"TONE GUIDE (do not copy, structure only):\n{canned_responses}"
    else:
        tone_block = ""

    brand_voice = """\
━━ HEADOUT BRAND VOICE ━━
- Warm and human, not corporate. Address the guest by first name if provided.
- Own the mistake without excessive apology. One "I'm sorry" is enough.
- Be specific: name the venue, the date, the concrete action taken/being taken.
- Never make claims without evidence from the timeline. If we resolved: say what.
  If we're still resolving: say what next step.
- Direct language over hedging. No "we sincerely appreciate your patience".
- Match language complexity to the guest's review — if they wrote 2 sentences,
  reply in 2-3 sentences. If they wrote a story, engage with the story.
- End with something the guest can do (link, timeline, contact) — not just "thanks".
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"""

    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.

REVIEW:
{review_text}

CLASSIFICATION: L1={l1}, L2={l2}
RESOLUTION: {resolution}
DSS: {json.dumps(dss_rec or {}, indent=2)}
{name_hint}

{tone_block}

{brand_voice}

INSTRUCTIONS:
1. Tone examples are reference only. Do not copy phrasing.
2. Reference the guest's SPECIFIC complaint in their own terms.
3. Compensation mentioned must match the resolution string exactly. Do NOT invent amounts.
4. Non-defensive acknowledgement.
5. Use guest's name if known; otherwise open warmly. Never leave literal placeholders.
6. 3-5 sentences. No bullets. No headings.
7. Return ONLY the reply text."""


# ─── 6b. Support event summarisation (Zendesk timeline → frames) ───────────
def support_event_prompt(event: dict, prev_event: dict | None,
                         next_event: dict | None) -> str:
    gap_list = "\n".join(f"  - {g}" for g in GAP_TAXONOMY)
    return f"""You are summarising ONE support interaction event from a Zendesk timeline
for an internal RCA dashboard at Headout.

=== PREVIOUS EVENT (context, may be null) ===
{json.dumps(prev_event or None, indent=2)}

=== THIS EVENT (summarise this one) ===
{json.dumps(event, indent=2)}

=== NEXT EVENT (context, may be null) ===
{json.dumps(next_event or None, indent=2)}

=== ALLOWED GAP LABELS ===
{gap_list}

RULES:
1. Neutral tone, facts only. Do NOT adopt or defend the guest's framing.
2. Do not fabricate. If a comment says nothing actionable, weDid = "No CE action on this thread" — don't invent.
3. Do not invent handles or timestamps. Use [placeholder] if a name/time is needed and unknown.
4. "gap" must be EXACTLY one of the allowed gap labels above, or an empty string "".
5. Support-failure-supersedes rule: if the underlying issue is external (weather/FM)
   but the CE mishandled the response, tag the gap on the CE side, not on the external event.

Return ONLY strict JSON:
{{"guestSaid": "...", "weDid": "...", "guestReply": "...", "gap": "..."}}"""


def support_arc_prompt(frames: list) -> str:
    return f"""Summarise the overall support interaction arc below in 2-3 neutral sentences
for an internal RCA dashboard. Facts only — no fabrication, no invented names,
timestamps, or compensation amounts. No adopting the guest's framing.

=== SUPPORT FRAMES ===
{json.dumps(frames or [], indent=2)}

Return ONLY the 2-3 sentence paragraph, no headings."""


# ─── 7. Venue extraction — multi-venue, for Tier 2 cascade ─────────────────
def venue_extraction_prompt(review_text: str) -> str:
    return f"""Read the following Trustpilot review. Extract EVERY venue or experience the guest mentions — even if multiple.

Rules:
- Return the shortest recognisable venue name only (e.g. "Vatican Museums", "Eiffel Tower", "Sagrada Familia", "Sistine Chapel").
- Do NOT include ticket variants, tour types, or descriptors ("guided", "priority", "combo", "with dinner", "skip-the-line").
- Return ALL venues mentioned, in order of appearance.
- If no clear venue can be identified, return an empty list.
- Do NOT invent — if not explicit, do not include.

REVIEW:
{review_text}

Return ONLY valid JSON, no markdown:
{{"venue_hints": ["...", "..."]}}"""


# ─── 8. Flag-to-Biz Slack message ──────────────────────────────────────────
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
