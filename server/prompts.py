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
    if lang and lang not in ("en", "auto", ""):
        return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
Return ONLY the translation. No preamble, no label, no explanation.

Original ({lang}):
{body}"""
    return f"""Detect the language of this Trustpilot review.
If it is already written in English, reply with exactly the word: ENGLISH_ALREADY
If it is in any other language, translate it into clear English — preserve tone exactly \
(frustration, sarcasm, urgency). Do not paraphrase.
Return ONLY the English translation, or the word ENGLISH_ALREADY. No preamble, no label.

Review:
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
def match_indicator_prompt(review_text: str, review_date: str) -> str:
    """Approved matching-indicator extraction (booking match, Tier 2)."""
    return f"""You are matching a Trustpilot review to a Headout booking. Read the review and
extract every indicator that could identify the booking. Do not invent anything —
only what the text supports.

REVIEW (posted {review_date or "unknown"}):
{review_text}

Return JSON:
- guest_name — from the reviewer name and any name mentioned in the text
- experience_or_venue — what they visited/booked, in their words
  (e.g. "Eiffel Tower summit", "Rome catacombs tour")
- city_or_country — if stated or clearly implied
- visit_date_hint — any date/time reference ("on May 2nd", "last Saturday",
  "two weeks ago") normalized to a best-guess date or range, given the review
  was posted {review_date or "unknown"}
- pax — how many people the booking was for, as a number. Count it from
  whatever the review says: "9 combo tickets" → 9, "my wife and I" → 2,
  "family of four" → 4, "2 adults 1 child" → 3. Null if not inferable.

Return ONLY valid JSON, no markdown:
{{"guest_name": "<or null>",
  "experience_or_venue": "<or null>",
  "city_or_country": "<or null>",
  "visit_date_hint": "<or null>",
  "pax": "<number or null>"}}

Every field above is consumed by the matcher:
1. guest_name — searched in Zendesk as the ticket requester, alongside the
   Trustpilot display name.
2. experience_or_venue + city_or_country — resolved to TGIDs, and scored by
   significant-word overlap against each candidate's experience name (weight 2x).
3. visit_date_hint — scored by closeness to each candidate's visit date, falling
   back to the review post date when no hint is present.
Highest combined score = best match shown first."""


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


# ─── 8. RCA v3 prompt ───────────────────────────────────────────────────────
def rca_v3_prompt(
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
    review_id: str = "",
    timeline_raw: list = None,
    ticket_facts: dict = None,
) -> str:
    """
    Generates RCA v3 shape: tldr, wwr_chain, prevention, evidence,
    issue_specific_answers, checklist_answers.

    ticket_facts: structured facts already extracted from the Zendesk tickets
    (guest_full_name, booking_status, refund {...}, ce_actions, resolution_summary,
    primary_issue, sla_breached, ticket_email_seen, evidence, ...). These are
    PRE-VERIFIED — prefer them over re-deriving the same facts from raw bodies.

    checklist: {"general": GENERAL_GUIDELINES, "ce": CE_ERROR_CHECKS,
                "ro": RO_ERROR_CHECKS, "scenarios": SCENARIO_CHECKS}
    timeline_raw: parallel list of raw Zendesk ticket comment bodies.

    Embedded rules (do NOT edit — verbatim from spec):
    • No fabrication. Every claim must be citeable from timeline/booking/review.
      Unknown → Unknown. No evidence → "not present in ticket or booking data".
    • GENERAL_GUIDELINES["rca_output"] rules folded into writing rules below.
    • Run ALL CE Error and RO Error checks every time.
    • From Scenario checklists, run ONLY the scenario(s) that fit the review.
    • Each check → Yes/No/Unknown/N/A + cite evidence (ticket id + line, or booking field).
    • checklist_answers item shape: {section, check, answer, evidence}.
    """
    tl_text = json.dumps(timeline[:30], indent=2) if timeline else "[]"
    bk_text = json.dumps({k: v for k, v in (booking or {}).items() if k != "_match"})
    in_text = json.dumps(insights or {})
    ds_text = json.dumps(dss_rec or {})

    # Zendesk raw ticket bodies
    if timeline_raw:
        zd_raw_lines = []
        for i, body in enumerate(timeline_raw[:20]):
            if body and body.strip():
                zd_raw_lines.append(f"[ticket_{i+1}] {body[:600]}")
        zendesk_raw_block = "\n".join(zd_raw_lines) if zd_raw_lines else "(no raw ticket bodies)"
    else:
        zendesk_raw_block = "(no raw ticket bodies)"

    # Pre-extracted structured ticket facts (verified upstream)
    _tf = {k: v for k, v in (ticket_facts or {}).items()
           if v not in (None, "", [], {}, "Unknown")}
    ticket_facts_block = json.dumps(_tf, indent=2) if _tf else "(no structured facts extracted)"

    # General guidelines → writing rules
    general = (checklist or {}).get("general", {})
    rca_output_rules = general.get("rca_output", [])
    writing_rules_block = ""
    if rca_output_rules:
        writing_rules_block = (
            "\n━━ RCA OUTPUT RULES (non-negotiable) ━━\n"
            + "\n".join(f"• {r}" for r in rca_output_rules)
            + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    # "What went wrong" mandated writing structure (Headout ORM guideline).
    wwr_structure = general.get("what_went_wrong_structure", [])
    wwr_structure_block = ""
    if wwr_structure:
        wwr_structure_block = (
            "\n━━ \"WHAT WENT WRONG\" — REQUIRED WRITING STRUCTURE ━━\n"
            "Structure the what-went-wrong content (the wwr_chain steps) to cover these\n"
            "5 sections in order. Headings 1–5 are mandatory; the (a)/(b)/(c) sub-points\n"
            "are indicative — use only those relevant. Be concise and focus on the\n"
            "OPERATIONAL failure; do NOT restate the review.\n"
            + "\n".join(f"{r}" for r in wwr_structure)
            + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    # CE and RO error check lists
    ce_checks = (checklist or {}).get("ce", [])
    ro_checks = (checklist or {}).get("ro", [])
    scenarios = (checklist or {}).get("scenarios", {})

    ce_block = ""
    if ce_checks:
        ce_block = (
            "\n━━ CE ERROR CHECKS — run ALL every time ━━\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(ce_checks))
            + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    ro_block = ""
    if ro_checks:
        ro_block = (
            "\n━━ RO ERROR CHECKS — run ALL every time ━━\n"
            + "\n".join(f"{i+1}. {c}" for i, c in enumerate(ro_checks))
            + "\n━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
        )

    scenario_block = ""
    if scenarios:
        sc_lines = ["━━ SCENARIO CHECKS — run ONLY the scenario(s) that fit this review ━━"]
        for name, items in scenarios.items():
            sc_lines.append(f"\n[{name}]")
            for i, item in enumerate(items):
                sc_lines.append(f"  {i+1}. {item}")
        sc_lines.append("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
        scenario_block = "\n" + "\n".join(sc_lines)

    return f"""You are an ORM analyst writing an internal Root-Cause Analysis (RCA).

REVIEW ID:      {review_id}
CLASSIFICATION: L1={l1}  L2={l2}  Sub-theme={sub_theme}

REVIEW TEXT:
{review_text}

BOOKING:
{bk_text}

ZENDESK TIMELINE (structured):
{tl_text}

=== ZENDESK TICKETS FOR THIS BOOKING (matched by booking_id + guest name) ===
{zendesk_raw_block}

=== VERIFIED TICKET FACTS (pre-extracted — trust these over re-deriving) ===
{ticket_facts_block}

INSIGHTS:
{in_text}

DSS RECOMMENDATION:
{ds_text}

SUPPORT SUMMARY:
{support_summary or "(none)"}
{writing_rules_block}
{wwr_structure_block}

━━ CORE RULES (non-negotiable) ━━
1. NO FABRICATION. Every claim in wwr_chain, evidence, checklist_answers must be
   citeable from the timeline, booking, or review_text above. If unknown → "Unknown".
   No evidence → write "not present in ticket or booking data".
2. NEUTRAL TONE. Facts only. Do not adopt or defend the guest's narrative.
3. tldr ≤ 25 words, one sentence, factual. Format: "what happened + what we're doing."
4. wwr_chain: follow the "WHAT WENT WRONG" required writing structure above —
   cover guest issue → claim accuracy (Yes/Partially True/No) → what actually
   happened (root cause / operational failure / SOP gap) → SP escalation (did CE
   escalate; if not, why) → fixes (teams tagged + corrective actions). Keep it
   causal and concise, not a restatement of the review. Up to ~6 steps.
   Each step: {{"step": N, "what": "...", "why": "..."}}.
5. prevention: ORM-ownable actions only. Pre-visit comms first. If cross-team action
   needed, label explicitly (e.g. "Product team:").
6. evidence: prefix each item with its source in square brackets: [timeline], [review],
   [booking], [insights]. Use verbatim quotes where possible.
7. issue_specific_answers: answer Yes/No/Unknown for checks relevant to L1={l1}.
   Short parenthetical (≤60 chars) only if timeline directly supports.
8. Support-failure supersedes: if an external event occurred BUT CE mishandled the
   guest contact, the root cause is the CE failure, not the external event.
9. No invented handles, timestamps, or comp amounts. Use [placeholder] if unknown.
10. VERIFIED TICKET FACTS above are already extracted and checked. When a fact you
    need (guest name, booking status, refund status/amount, CE actions, resolution,
    SLA breach, primary issue) is present there, USE IT — do not contradict it or
    re-derive a different value from the raw bodies. Raw bodies are for detail the
    facts block does not already cover.
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

=== DIAGNOSTIC CHECKS — VERIFY, DON'T GUESS ===
- Run ALL CE Error and RO Error checks, every time.
- From the Scenario checklists, run ONLY the scenario(s) that fit this review + classification.
- Each check → Yes / No / Unknown / N/A AND cite the evidence: ticket id + the line, or the
  booking field. No evidence → "not present in ticket or booking data". Never guess.
{ce_block}
{ro_block}
{scenario_block}

checklist_answers item shape:
{{"section": "ce" | "ro" | "<scenario name>", "check": "...", "answer": "Yes|No|Unknown|N/A", "evidence": "...", "zd_ref": "ZD-<ticket id> or ''"}}
zd_ref: the Zendesk ticket id the evidence comes from (e.g. "ZD-31055921").
Empty string when the evidence is from booking data or no ticket applies.

Return ONLY valid JSON (no markdown fences) matching this exact shape:
{{
  "tldr":                    "<≤25 words, one sentence>",
  "wwr_chain":               [{{"step": 1, "what": "...", "why": "..."}}, ...],
  "prevention":              "<1-2 sentences>",
  "evidence":                ["[source] quote", ...],
  "issue_specific_answers":  {{"<key>": "Yes|No|Unknown (<optional note>)", ...}},
  "checklist_answers":       [{{"section": "ce|ro|<scenario>", "item": "<question>", "answer": "Yes|No|Unknown|N/A", "evidence": "<cite or not present in ticket or booking data>", "zd_ref": "ZD-... or ''"}}, ...]
}}"""


# ─── 9b. WWR analysis — stacked scenario blocks (Task #13 §3) ───────────────
def wwr_analysis_prompt(
    review_text: str,
    timeline: list,
    ticket_facts: dict,
    booking: dict,
    l1: str, l2: str, sub_theme,
    primary_scenario, overlay_scenarios: list,
) -> str:
    """One block per applicable scenario: accurate? / why / fix."""
    from server.checklist import GENERAL_GUIDELINES, SCENARIO_CHECKS
    scen_list = [s for s in ([primary_scenario] + list(overlay_scenarios or [])) if s]
    if not scen_list:
        scen_list = ["CE-error review"]  # CS non-refund path: audit CE handling
    scen_lines = []
    for s in scen_list:
        checks = SCENARIO_CHECKS.get(s, [])
        scen_lines.append(f"- {s}" + (f" (checks: {'; '.join(checks[:4])}…)" if checks else ""))
    rules = "\n".join(f"• {r}" for r in GENERAL_GUIDELINES.get("rca_output", []))
    return f"""You are writing the "What Went Wrong" section of an internal Headout ORM RCA.

REVIEW:
{review_text}

CLASSIFICATION: L1={l1}  L2={l2}  Sub-theme={sub_theme or "—"}

APPLICABLE SCENARIOS (primary first — address EACH separately, in this order):
{chr(10).join(scen_lines)}

ZENDESK TIMELINE:
{json.dumps((timeline or [])[:25], ensure_ascii=False)}

VERIFIED TICKET FACTS:
{json.dumps({k: v for k, v in (ticket_facts or {}).items() if v not in (None, "", [], {})}, ensure_ascii=False)}

BOOKING:
{json.dumps({k: v for k, v in (booking or {}).items() if k != "_match"}, ensure_ascii=False)}

RULES:
{rules}
• For each scenario produce EXACTLY three bullets: is the guest's claim accurate
  (Yes/Partially/No + one sentence citing evidence), why it happened (one sentence,
  root cause grounded in the timeline/facts), and the fix (one sentence action +
  owning team).
• Ground every claim in the timeline, ticket facts, or booking. Never invent.
• No prose prefix, no priority-rule restatement, no restating the review.

Return ONLY valid JSON (no markdown fences):
{{"scenarios": [
  {{"scenario_name": "<name>", "is_primary": true|false,
    "accuracy": "Yes|Partially|No",
    "accuracy_explanation": "<one sentence citing evidence>",
    "why": "<one sentence root cause>",
    "fix": "<one sentence action + owning team>"}}
]}}"""


# ─── 9a. Zendesk timeline shaping prompt ────────────────────────────────────
def _fmt_date_ist(dt_str: str) -> str:
    """Convert ISO date/datetime string → 'DD Mon HH:MM IST' (or 'DD Mon YYYY' if date-only)."""
    if not dt_str:
        return "unknown"
    try:
        from datetime import datetime, timezone, timedelta
        IST = timezone(timedelta(hours=5, minutes=30))
        s = str(dt_str).strip()
        if "T" in s or (len(s) > 10 and ":" in s[10:]):
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(IST).strftime("%d %b %H:%M IST")
        dt = datetime.strptime(s[:10], "%Y-%m-%d")
        return dt.strftime("%d %b %Y")
    except Exception:
        return str(dt_str)[:16]


def zendesk_timeline_shape_prompt(
    booking: dict,
    review_body: str,
    review_pub_date: str,
    raw_events: list,
) -> str:
    """
    Instructs Claude to batch-shape raw Zendesk events into a clean, structured
    timeline. Each kept event has: idx_range, time, thread, actor, label, summary, keep.

    Changes vs previous version:
    - Booking creation date pre-formatted in IST style (consistent with Zendesk times).
    - "Booking created" summary uses visit date only — no full experience name.
    - Hard drop list for bot/selenium/chat-session/tag noise events.
    - Summary rules: guest events must state WHY they contacted; CE events must state
      the specific action taken.
    """
    bk = booking or {}
    booking_date_fmt = _fmt_date_ist(bk.get("date_of_booking") or bk.get("creationDate") or "")
    visit_date_raw   = bk.get("visitDate") or bk.get("date_of_visit") or ""
    visit_date_fmt   = _fmt_date_ist(visit_date_raw) if visit_date_raw else "the visit date"

    booking_summary = {k: v for k, v in bk.items()
                       if k not in ("_match", "timeline_raw")}
    events_json = json.dumps(raw_events or [], indent=2)
    booking_json = json.dumps(booking_summary, indent=2)

    return f"""You are shaping raw Zendesk support events into a clean, human-readable
timeline for an internal ORM dashboard. Headout CX analysts will read this — it must
be factual, concise, and completely free of system noise.

=== BOOKING METADATA ===
{booking_json}

=== REVIEW ===
Published: {review_pub_date or "unknown"}
Body: {(review_body or "")[:600]}

=== RAW EVENTS (idx = sequential order) ===
{events_json}

=== WHAT THIS TIMELINE IS ===
A clear, human story of the guest's journey — the booking, any contact with
support, what we did in response, and how it ended. It is NOT a system log. A CX
analyst should read it top-to-bottom and instantly understand: did the guest
reach out, HOW (channel), WHY (what they asked), WHAT we did or offered, and
whether the booking was fulfilled / resolved.
=== INSTRUCTIONS ===
1. INJECT two bookend events (not present in raw_events):
   - FIRST — Booking created:
     {{"idx_range": [], "time": "{booking_date_fmt}",
       "thread": "booking", "actor": "creation",
       "label": "Booking created",
       "summary": "<WHAT the guest actually booked — variant / pax / options selected, and notably any upsell or add-on NOT selected at checkout (e.g. '2nd Floor only — Summit upsell not selected at checkout'). Draw this from the booking metadata. Do NOT write the full experience name.>", "keep": true}}
   - LAST — Review posted:
     {{"idx_range": [], "time": "{review_pub_date or 'unknown'}",
       "thread": "review", "actor": "review",
       "label": "Review posted", "summary": "Negative Trustpilot review posted, BID referenced.", "keep": true}}
2. KEEP only events that are part of the guest's story:
   - The guest contacting us (any channel)
   - Our substantive response to the guest (what we said / did / offered)
   - Ticket / voucher delivery or fulfilment (and when)
   - Any refund, credit, cancellation, reschedule, or escalation outcome
   Everything else → keep: false.
3. DROP (keep: false) — internal noise; never show these:
   - Bot / AI tagging or classification (interaction tags like "delay_fulfilment…")
   - "AI-resolved", chat-summary, or internal CE-summary log entries
   - Chat-transcript dumps and "conversation opened / transcript logged" system rows
   - Pseudo-email / vendor-login / password / credential generation
   - Macro floods, field / tag updates, assignment logs
   - Email signatures, logos, legal footers, blank bodies
4. THREAD (the channel chip) — how the event happened:
   - "booking" → booking-side events: Booking created, Tickets / voucher sent,
                 Refund issued, Booking cancelled
   - "review"  → the Review posted bookend
   - "email"   → email thread with the guest
   - "chat"    → live chat / Skyler / web-user conversation
   - "call"    → phone call
   - "sp"      → correspondence with the supply partner / operator
   Infer it from the raw body. Do NOT default everything to "email".
5. SUMMARIES — write for a human, ONE clear sentence (max ~160 chars):
   - Guest contact → say WHY they reached out / what they asked.
     e.g. "Guest asked why their tickets hadn't arrived and needed them immediately for a same-day plan."
   - Our response → say WHAT WE DID or OFFERED (more time, resent tickets, credits, refund, escalation).
     e.g. "Explained tickets would arrive within 2 hours and stay valid until Jul 2027; could not expedite."
   - Fulfilment → WHAT was delivered and WHEN.
     e.g. "Tickets delivered to the guest, about 28 minutes after booking."
   - Refund / outcome → the amount and terms.
     e.g. "Full refund of USD 19.42 issued as an out-of-policy exception."
   Strip all HTML / signatures. Never quote raw JSON. Never adopt the guest's emotional wording.
6. LABELS — short and plain, from this vocabulary:
   "Booking created", "Tickets sent", "Guest reached out", "Guest reply",
   "CE response", "SP response", "Refund issued", "Booking cancelled",
   "Escalated to SP", "Review posted".
   Rules: the guest's FIRST contact → "Guest reached out"; a later guest message
   → "Guest reply"; our reply to the guest → "CE response"; a supply-partner reply
   → "SP response". No ticket IDs, no "[ZD-xxxxx]", no "(×N)" suffixes.
7. COLLAPSE consecutive events about ONE action (same moment) into a single event;
   list every collapsed idx in idx_range. Do NOT emit "(×N)" in the label.
8. TIME — copy each raw event's timestamp exactly as given (already 'DD Mon HH:MM').
   The bookends use the booking / review date-time as 'DD Mon HH:MM' when a clock
   time is available, else 'DD Mon'. Keep the format consistent across every event —
   never emit a raw ISO date like "2026-07-22".
9. ORDER — Booking created first, kept events in chronological order, Review posted last.
Return ONLY valid JSON — a list of shaped event objects, nothing else:
[
  {{"idx_range": [], "time": "...", "thread": "...", "actor": "...", "label": "...", "summary": "...", "keep": true}},
  ...
]"""


# ─── 10. Ticket Fact Extraction ─────────────────────────────────────────────
def ticket_extraction_prompt(
    booking: dict,
    timeline_raw: list,
    timeline_raw_ticket_ids: list | None = None,
) -> str:
    """
    Extract structured facts from raw Zendesk ticket comments for a booking.

    Accepts a booking dict, a list of raw comment bodies (timeline_raw), and
    an optional parallel list of Zendesk ticket IDs per comment body.
    Returns a JSON object matching the data-extraction-engine spec exactly.
    """
    booking_json = json.dumps(
        {k: v for k, v in (booking or {}).items() if k != "_match"},
        indent=2,
    )
    tids = timeline_raw_ticket_ids or []
    raw_lines = []
    for i, body in enumerate(timeline_raw or []):
        body_str = str(body).strip() if body else ""
        if not body_str:
            continue
        zd_id = tids[i] if i < len(tids) and tids[i] else ""
        label = f"ZD-{zd_id}" if zd_id else f"comment_{i+1}"
        raw_lines.append(f"[{label}]\n{body_str}")
    timeline_text = "\n\n---\n\n".join(raw_lines) if raw_lines else "(no ticket comments)"

    return f"""SYSTEM:
You are a data-extraction engine for Headout's ORM system. You read the raw
Zendesk support-ticket comments for ONE booking and extract structured facts.
You NEVER invent data. If a fact is not explicitly present in the tickets,
return null. Every value must be directly copyable from the ticket text.

USER:
=== BOOKING (from BigQuery — authoritative for IDs/dates) ===
{booking_json}

=== ZENDESK TICKET COMMENTS (chronological, raw bodies) ===
{timeline_text}

Extract the following using ONLY the ticket text and booking above.
Return null for anything not explicitly stated. Do not guess or infer.

Return STRICT JSON, no markdown:
{{
  "guest_full_name":   null,
  "booking_status":    null,
  "is_same_day_booking": null,
  "is_cancellable":    null,
  "is_reschedulable":  null,
  "sla_breached":      null,
  "ticket_email_seen": null,
  "interaction_tags":  [],
  "delay_or_issue_reason": null,
  "refund": {{
    "issued":        null,
    "amount":        null,
    "reference_id":  null,
    "out_of_policy": null
  }},
  "ce_actions": [],
  "resolution_summary": null,
  "primary_issue":      null,
  "evidence": {{}}
}}

RULES:
1. Null over guessing. If it's not in the text, it's null.
2. guest_full_name: only a human name that appears in the prose. If only a hash/base64 string exists, return null.
3. Copy amounts, reference IDs, and tags verbatim — never reformat or round.
4. booking_status only from an explicit status line, not inferred from tone.
5. Every non-null fact must have a matching ticket id in "evidence" using the format "ZD-<ticket id>" from the comment labels above.
"""


# ─── 9. Flag-to-Biz Slack message ──────────────────────────────────────────
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
