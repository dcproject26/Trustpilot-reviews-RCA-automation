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
# ─── Guest-facing copy, loaded from content/orm_macros.yaml ────────────────
# The brand voice, the takedown lines, the untraceable reply and the macro tag
# vocabulary all live in that file so CX and content can edit them without
# touching code. Everything below is the fallback: if the file is missing or
# a YAML edit is malformed, the app keeps running on the last known-good copy
# rather than shipping a broken or empty reply to a guest.
import logging as _logging
import os as _os

# This module had no logger. _load_macros() below reports a bad edit through
# one, and without it the FALLBACK PATH ITSELF raised NameError - so a typo in
# the copy file would take the app down instead of falling back to known-good
# copy, which is the exact opposite of what the fallback is for.
log = _logging.getLogger(__name__)

_MACROS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "content", "orm_macros.yaml")

_FALLBACK = {
    "brand_voice": (
        "CONVERSATIONAL, clear and concise. American English. Address the "
        "guest with \"Hey <first name>,\". No invented facts, no hyperbole."),
    "sign_off": "Best,\n[Your Name], Headout",
    "takedown": {
        "lines": {
            "a": {"text": "Glad we could make things right. If you have a moment, "
                          "you can update your TP review here: [link]",
                  "when": "a clean resolution"},
            "b": {"text": "Thanks for giving us the chance to fix things. If you're "
                          "open to it, feel free to update your review here: [link]",
                  "when": "we corrected our own error"},
            "c": {"text": "Thanks for bearing with us. If you'd like, you can update "
                          "your TP review here: [link]",
                  "when": "the guest waited, or the outcome is partial"},
        },
        "suppress_when": "The guest's tone is abusive, or the case has been "
                         "escalated more than once.",
    },
    "untraceable_reply": (
        "Hey {first_name},\n\nI'm sorry things didn't go as planned, and I'd love "
        "to fix this for you right away. Please share your booking ID (if "
        "available) or the email address used for your booking at "
        "https://bit.ly/hedout. Once we have your details, our team will dive "
        "right in to resolve it ASAP.\n\nThank you so much for your understanding "
        "and patience. I'll make sure we turn this around for you!"),
    "fallback_first_name": "there",
    "honorifics": ["mr", "mrs", "ms", "miss", "dr", "herr", "frau", "monsieur",
                   "madame", "sr", "sra", "don"],
    "macro_tags": {"trustpilot": [], "social": [], "twitter": []},
}


def _load_macros() -> dict:
    """Read the copy file, falling back field by field.

    Field-by-field matters: someone deleting one key while editing should not
    blank the other four. Anything the file does not define keeps its
    fallback.
    """
    data = {}
    try:
        import yaml
        with open(_MACROS_PATH, encoding="utf-8") as f:
            loaded = yaml.safe_load(f)
        if isinstance(loaded, dict):
            data = loaded
        else:
            log.error("[macros] %s did not parse as a mapping - using fallbacks",
                      _MACROS_PATH)
    except FileNotFoundError:
        log.warning("[macros] %s not found - using built-in copy", _MACROS_PATH)
    except Exception as e:
        log.error("[macros] %s could not be read (%s) - using built-in copy. "
                  "Run tools/check_macros.py to see what is wrong.",
                  _MACROS_PATH, e)
    merged = dict(_FALLBACK)
    for k, v in (data or {}).items():
        if v not in (None, "", [], {}):
            merged[k] = v
    return merged


MACROS = _load_macros()

BRAND_VOICE = ("━━ HEADOUT VOICE AND TONE - FOLLOW STRICTLY ━━\n"
               + str(MACROS["brand_voice"]).rstrip() + "\n"
               + "- Sign off exactly:\n      "
               + str(MACROS["sign_off"]).rstrip().replace("\n", "\n      ") + "\n"
               + "━" * 78)

TAKEDOWN_LINES = {k: v["text"] for k, v in MACROS["takedown"]["lines"].items()}

UNTRACEABLE_REPLY = (str(MACROS["untraceable_reply"]).rstrip() + "\n\n"
                     + str(MACROS["sign_off"]).rstrip())

# Also used by booking matching, not only by the greeting: a Trustpilot display
# name of "Frau Nicole" must not be searched for as a guest name. One list, in
# the copy file, so adding a title fixes both places at once.
HONORIFICS = {str(h).strip().lower().rstrip(".")
              for h in (MACROS.get("honorifics") or []) if str(h).strip()}


def strip_honorifics(name: str) -> str:
    """A person's name with any leading title removed."""
    parts = [p for p in str(name or "").replace(",", " ").split() if p]
    while parts and parts[0].strip().lower().rstrip(".") in HONORIFICS:
        parts.pop(0)
    return " ".join(parts)


def macro_tags(channel: str = "trustpilot") -> list:
    """The situation vocabulary the team already uses, for one channel."""
    return list((MACROS.get("macro_tags") or {}).get(channel) or [])


def takedown_block(verdict: str) -> str:
    """The takedown instruction for the response prompt."""
    if str(verdict or "").strip().lower() != "yes":
        return ("TAKEDOWN: not requested for this review. Do NOT add any line "
                "asking the guest to update their review.")
    td = MACROS["takedown"]
    lines = "\n".join(f'    {k}) "{v["text"]}"'
                      for k, v in sorted(td["lines"].items()))
    when = "\n".join(f'    {k}) {v.get("when", "")}'
                     for k, v in sorted(td["lines"].items()))
    return f"""━━ TAKEDOWN REQUESTED ━━
Add EXACTLY ONE of these lines, verbatim, as its own paragraph immediately
BEFORE the sign-off. Do not reword it, do not merge it into another sentence.
{lines}
Choose by situation:
{when}
DO NOT add any of them, and put nothing in their place, when:
    {str(td.get("suppress_when", "")).strip()}
{"━" * 78}"""


def response_draft_prompt(
    review_text: str, l1: str, l2: str, resolution: str,
    canned_responses: str = "", guest_name: str = "",
    dss_rec: dict | None = None,
    canned_list: list | None = None,
    takedown_verdict: str = "",
) -> str:
    name_hint = (f"The guest's first name is {guest_name}. Open with "
                 f'"Hey {guest_name},".' if guest_name
                 else 'No name is known - open with "Hey there,".')

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

    brand_voice = BRAND_VOICE

    takedown_block_text = takedown_block(takedown_verdict)
    return f"""You are drafting a public reply to a Trustpilot review on behalf of Headout's CX team.

REVIEW:
{review_text}

CLASSIFICATION: L1={l1}, L2={l2}
RESOLUTION: {resolution}
DSS: {json.dumps(dss_rec or {}, indent=2)}
{name_hint}

{tone_block}

{brand_voice}

{takedown_block_text}

INSTRUCTIONS:
1. Tone examples are reference only. Do not copy phrasing.
2. Reference the guest's SPECIFIC complaint in their own terms. The reply must
   answer what THIS guest actually raised - a macro that ignores their issue is
   worse than no macro.
3. Compensation mentioned must match the resolution string exactly. Do NOT invent amounts.
4. Non-defensive acknowledgement.
5. Open with "Hey <first name>," using the name given above. If no name is
   known, open "Hey there,". Never leave a literal placeholder like <Name>.
6. 3-5 sentences. No bullets. No headings.
7. Sign off on its own two lines, exactly:
   Best,
   [Your Name], Headout
8. Return ONLY the reply text."""


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
def match_indicator_prompt(review_text: str, review_date: str,
                           reviewer_name: str = "") -> str:
    """Approved matching-indicator extraction (booking match, Tier 2)."""
    return f"""You are matching a Trustpilot review to a Headout booking. Read the review and
extract every indicator that could identify the booking. Do not invent anything —
only what the text supports.

REVIEWER NAME: {reviewer_name or "(not provided)"}

REVIEW (posted {review_date or "unknown"}):
{review_text}

Return JSON:
- guest_name — copy the REVIEWER NAME above verbatim, unless the text clearly
  names a different person as the booker, in which case use that. It is often
  the only indicator available, so never omit it. If and only if the reviewer
  name is "(not provided)" and the text names nobody, return null — never the
  word "unknown".
- experience_or_venue — what they visited/booked, in their words
  (e.g. "Eiffel Tower summit", "Rome catacombs tour").
  IMPORTANT: the review may end with a line like "Reference number: <text>".
  Guests routinely type the VENUE there instead of a booking number — e.g.
  "Reference number: Salt mines Krakow" means the venue is "Salt mines Krakow".
  If that line holds anything other than a plain number, read it as the venue.
- city_or_country — if stated or clearly implied
- visit_date_hint — the date the guest VISITED or was due to visit. Not the
  date they booked, not the date they were emailed, not the date they
  complained. "I booked yesterday" is a booking date and must be ignored;
  "we went last Saturday" or "our visit on the 14th" is a visit date. Resolve
  it against the post date {review_date or "unknown"}.
  Output a BARE DATE, exactly YYYY-MM-DD, and nothing else — no ranges, no
  alternatives, no explanation. If two dates are equally likely, pick the more
  likely one. Null if the review gives no date reference at all.
- pax — how many people the booking was for, as a number. Count it from
  whatever the review says: "9 combo tickets" → 9, "my wife and I" → 2,
  "family of four" → 4, "2 adults 1 child" → 3. Null if not inferable.
- issue_terms — 2 to 5 SHORT search phrases naming the PROBLEM the guest
  had, the way it would appear in a support ticket. This is how the booking
  gets found when the review carries no booking id: the guest almost always
  contacted support about the same problem first, so the problem itself is
  an identifier.
  Give each phrase TWICE when the review is not in English - once in the
  review's own language and once in English - because the support ticket
  will be in the guest's language.
  Example, a German review about a voucher showing the wrong date:
      ["falsches Datum", "wrong date", "Voucher", "voucher", "Musical"]
  Name the problem, not the emotion: "wrong date on voucher", not
  "unbelievable". 2-4 words each. Null if the review states no concrete
  problem.
- dates_mentioned — EVERY date the review names, as YYYY-MM-DD, in the order
  they appear. Not just the visit date: a review that says "I bought for
  20.10 but the voucher said 20.06" names two, and BOTH are searchable - one
  is the booking the guest wanted, the other is what the system produced.
  Use the post date {review_date or "unknown"} to resolve a bare "20.10" to a
  year. Empty list if none.
- outcome — what the guest says HAPPENED at the end, one of exactly:
  "refund_denied", "refund_given", "no_response", "unresolved",
  "resolved", or null. "nothing could be done" is refund_denied.
  "weeks of chats with no solution" is unresolved.

Return ONLY valid JSON, no markdown:
{{"guest_name": "<or null>",
  "experience_or_venue": "<or null>",
  "city_or_country": "<or null>",
  "visit_date_hint": "<or null>",
  "pax": "<number or null>",
  "issue_terms": ["<phrase>", "..."],
  "dates_mentioned": ["YYYY-MM-DD", "..."],
  "outcome": "<or null>"}}

Every field above is consumed by the matcher:
1. guest_name — searched in Zendesk as the ticket requester, alongside the
   Trustpilot display name.
2. experience_or_venue + city_or_country — resolved to TGIDs, and scored by
   significant-word overlap against each candidate's experience name (weight 2x).
3. visit_date_hint — scored by closeness to each candidate's visit date, falling
   back to the review post date when no hint is present.
4. issue_terms — searched against the TEXT of support tickets. A guest who
   describes a problem in a review almost always raised the same problem with
   support first, so the problem wording finds the ticket, and the ticket
   carries the booking id. This is the path that matches a review with no
   booking id and no recognisable venue.
5. dates_mentioned — matched against the dates on candidate tickets and
   bookings. A review naming both the intended date and the wrong one gives
   two chances to match instead of one.
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
    scenarios_routed: list = None,
    issue_questions: list = None,
    canned_list: list = None,
) -> str:
    """
    Generates the RCA v3 shape: tldr {our_mistake, our_fix}, what_went_wrong
    (the 5 mandated headings), booking_logs, flags (checklist run silently,
    failures only), support_interaction / sp_interaction / sop_compliance
    (each carrying zd_ref), issue_specific_answers, takedown.

    Benched against a real draft in tools/try_rca_prompt.py before shipping;
    that file carries the same template - edit there first, ship here after
    the audit passes.

    ticket_facts: PRE-VERIFIED structured facts - prefer over re-deriving.
    checklist: {"general", "ce", "ro", "scenarios"}.
    scenarios_routed: primary + overlay scenario names; only their checklists
    go into the prompt (the flags section says "every routed scenario").
    """
    bk = {k: v for k, v in (booking or {}).items()
          if k not in ("_match", "timeline_raw")}

    raw_lines = []
    for i, body in enumerate((timeline_raw or [])[:20]):
        if body and str(body).strip():
            raw_lines.append(f"[ticket_{i+1}] {str(body)[:600]}")

    _tf = {k: v for k, v in (ticket_facts or {}).items()
           if v not in (None, "", [], {}, "Unknown")}

    def _block(title, items):
        if not items:
            return ""
        return ("\n━━ " + title + " ━━\n"
                + "\n".join(f"{i+1}. {c}" for i, c in enumerate(items))
                + "\n" + "━" * 40)

    routed = [s for s in (scenarios_routed or [])
              if s in (checklist or {}).get("scenarios", {})]
    sc_lines = []
    for name in routed:
        sc_lines.append(f"[{name}]")
        sc_lines.extend(f"  {i+1}. {it}" for i, it
                        in enumerate(checklist["scenarios"][name]))
    scenario_block = ""
    if sc_lines:
        scenario_block = ("\n━━ SCENARIO CHECKS - every routed scenario, run all ━━\n"
                          + "\n".join(sc_lines) + "\n" + "━" * 40)

    # Approved replies, as VOICE only. Output rule 18 is what keeps them from
    # becoming content: a tone example sitting next to a "write a reply"
    # instruction is the easiest way to get a canned answer with this guest's
    # name pasted into it. Two or three is enough to establish a register;
    # more starts reading like a pattern to match.
    if canned_list:
        tone = "\n\n".join(
            f"Example {i} [{(ex.get('situation') or '').strip()}]:\n"
            f"{(ex.get('response') or '').strip()}"
            for i, ex in enumerate(canned_list[:3], 1))
    else:
        # Named, not blank. A silent absence reads as "no voice to match";
        # this says the reply was written without one, which is a fact the
        # associate reviewing it should have.
        tone = "(no approved replies matched — write in plain, warm, direct English)"

    out = RCA_V3_TEMPLATE
    for token, value in {
        "<<CANNED_TONE>>":      tone,
        "<<REVIEW_ID>>":        review_id,
        "<<L1>>":               l1 or "",
        "<<L2>>":               l2 or "",
        "<<SUB_THEME>>":        sub_theme or "",
        "<<SCENARIOS_ROUTED>>": ", ".join(routed) or "(none routed)",
        "<<REVIEW_TEXT>>":      review_text or "",
        "<<BOOKING>>":          json.dumps(bk, default=str),
        "<<TIMELINE>>":         json.dumps((timeline or [])[:30], indent=2, default=str),
        "<<ZENDESK_RAW>>":      "\n".join(raw_lines) or "(no raw ticket bodies)",
        "<<TICKET_FACTS>>":     (json.dumps(_tf, indent=2, default=str)
                                 if _tf else "(no structured facts extracted)"),
        "<<INSIGHTS>>":         json.dumps(insights or {}, default=str),
        "<<DSS>>":              json.dumps(dss_rec or {}, default=str),
        "<<SUPPORT_SUMMARY>>":  support_summary or "(none)",
        "<<CE_BLOCK>>":         _block("CE QA AREAS - guest-facing handling",
                                       (checklist or {}).get("ce", [])),
        "<<RO_BLOCK>>":         _block("RO QA AREAS - fulfilment and escalation",
                                       (checklist or {}).get("ro", [])),
        "<<SCENARIO_BLOCK>>":   scenario_block,
        "<<ISSUE_QUESTIONS>>":  ("\n".join(f"- {q}" for q in (issue_questions or []))
                                 or "- (none supplied)"),
        # The two dates the warehouse always knows. Rule 10b asks for them as
        # bookends on an otherwise undated sequence, so they have to be handed
        # over explicitly rather than left for the model to dig out of the
        # booking JSON — which is what it was already failing to do.
        "<<BOOKING_DATE>>":     _bookend(bk, "date_of_booking", "creationDate",
                                         "bookingDate"),
        "<<VISIT_DATE>>":       _bookend(bk, "visitDate", "date_of_visit",
                                         "experienceDate"),
    }.items():
        out = out.replace(token, str(value))
    return out


# Data blocks are injected by token replacement (<<BOOKING>> etc.), not
# str.format - the output shape below is full of JSON braces and doubling
# every one of them is exactly how a template stops matching its bench copy.
# The RCA prompt. v4 replaced v3 wholesale: findings now hang off the guest
# issue they explain instead of pooling at document level, evidence carries
# {text, source, ref} instead of a "[booking] …" prefix, claim_accuracy is a
# closed four-value enum, and issue_specific_answers is an array rather than
# a {question: answer} map. The token contract is unchanged, so rca_v3_prompt()
# below needs no new arguments.
#
# Data blocks are injected by token replacement (<<BOOKING>> etc.), not
# str.format - the output shape below is full of JSON braces and doubling
# every one of them is exactly how a template stops matching its bench copy.
RCA_V4_TEMPLATE = """You are an ORM analyst at Headout writing an internal Root-Cause Analysis.

WHO READS THIS: CX leadership in a Slack thread. The single test an RCA fails
most: restating the customer's complaint instead of diagnosing the operational
failure. "Guest couldn't find the guide" is a symptom. "The MP field still
showed the old point" is a root cause. Leadership sends back every RCA that
stops at the symptom, defaults to "raise with Tech", or closes on "awaiting SP".

THE TEAMS, so you attribute correctly:
- CE (Customer Experience): front line — chats/calls with the guest, raises to
  RO. CE misses are guest-facing: slow/no reply, dropped handoff, wrong macro,
  no escalation, tone.
- RO (Reservation Ops): back line — fulfilment, SP escalations, vendor issues.
  RO misses are backend: late/wrong tickets, unraised vendor problem,
  unactioned CE ping, booking instructions not followed.
- SP (Supply Partner): the vendor. Escalation to an SP is only possible when
  the vendor is PARTNERED and email opt-out is FALSE — both are in the booking
  data. A blocked escalation is a fact to state, not a miss.

WHERE FACTS LIVE — the only sources you may verify against, routed by claim.
Each maps to a `source` value used in `evidence[]` and `issue_specific_answers[]`:

  source = "exp-page"  → INSIGHTS.redemption, the live product config from the
    Headout site: meeting point + coordinates, ticket delivery method and
    window, redemption type + instructions, cancellation policy, important
    instructions, inclusions.
    Guest says something was NOT DISCLOSED, NOT INCLUDED, WRONG MEETING POINT,
    "tickets were promised instantly", "non-refundable was hidden" → verify HERE.

  source = "booking"   → the BigQuery booking dump: variant, pax, amount paid,
    booking status, fulfilment vendor, isPartnered, escalation email.
    Guest claims about what was bought, paid, cancelled → verify HERE.

  source = "bms"       → the BMS record: voucher issued, ticket artefacts,
    seat/slot assignment.
    Claims about what the guest actually received → verify HERE.

  source = "zendesk"   → timeline + raw ticket bodies + VERIFIED TICKET FACTS:
    what the guest told us, what CE/RO did and when, refunds actioned, SP side
    conversations.
    Claims about support conduct → verify HERE.

  source = "insights"  → BigQuery aggregates: similar-review counts,
    similar-support counts, completion rates, ratings, and the window they cover.
    Pattern and recurrence claims → verify HERE.

  source = "dss"       → the DSS sheet: the SOP needle our own decision sheet
    prescribes for this situation. A playbook lookup, never a warehouse
    aggregate.
    Policy questions — what we were supposed to do → verify HERE.

If the needed source is absent (redemption null, no tickets found), the
evidence text says so plainly and `ref` is null — never guess, and weigh
whether the missing data is itself a flag.

REVIEW ID:        <<REVIEW_ID>>
CLASSIFICATION:   L1=<<L1>>  L2=<<L2>>  Sub-theme=<<SUB_THEME>>
ROUTED SCENARIOS: <<SCENARIOS_ROUTED>>

Copy the CLASSIFICATION tokens verbatim into `l1`, `l2` and `sub_themes`. They
come from the upstream classifier, which has already applied the priority rules,
and the dashboard's selects are populated from the same taxonomy — so a
rephrased category matches no row. Do not re-derive them, do not abbreviate
them, do not drop a letter prefix. `overlay_scenarios` is the only
classification field you produce yourself.

REVIEW TEXT:
<<REVIEW_TEXT>>

BOOKING:
<<BOOKING>>

ZENDESK TIMELINE (structured):
<<TIMELINE>>

=== ZENDESK TICKETS FOR THIS BOOKING (raw bodies) ===
<<ZENDESK_RAW>>

=== VERIFIED TICKET FACTS (pre-extracted — trust these over re-deriving) ===
<<TICKET_FACTS>>

INSIGHTS (incl. experience-page redemption data, similar-review and
similar-support counts, completion rates, and the window they cover):
<<INSIGHTS>>

DSS RECOMMENDATION (SOP needle; {} or match_score 0 = needle unavailable):
<<DSS>>

SUPPORT SUMMARY:
<<SUPPORT_SUMMARY>>

APPROVED REPLY VOICE — tone reference only, never content to copy:
<<CANNED_TONE>>

━━ ISSUE-SPECIFIC QUESTIONS — answer each verbatim as a key ━━
<<ISSUE_QUESTIONS>>
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

THE QA AREAS BELOW ARE A COVERAGE GUIDE FOR THE RCA, NOT A TEAM SCORECARD.
Use them to check that this RCA raises what the teams need to act on. An area
that turned out fine is silence — never a line in the output.
<<CE_BLOCK>>
<<RO_BLOCK>>
<<SCENARIO_BLOCK>>

━━ CORE RULES ━━

1. NO FABRICATION. Every statement is citeable from the data above. No
   evidence → say so in the evidence text and set `ref` null. Never invent
   handles, timestamps or amounts; use [placeholder] if a value is unknown.
   Trust VERIFIED TICKET FACTS over re-deriving them.

2. EVERY ISSUE, SEPARATELY. A review can raise several distinct complaints.
   Return one `guest_issues` object per complaint. Each carries its OWN
   root_cause, operational_failure, sop_gap, pattern and fix — never pooled,
   never merged. Do not invent a second issue when the guest raised one.

3. DIAGNOSE, DON'T DESCRIBE. Name the concrete failing step. Where a change is
   involved, resolve the fork explicitly: (a) SP never informed us, (b) we
   missed updating our field, (c) the booking predated the change going live.
   NEVER accepted as a root cause: a restatement of the review, "awaiting SP",
   or "raised with Tech" without the technical-vs-operational call.

4. CHECK OUR OWN CONFIG BEFORE BLAMING THE SP: variant naming, meeting-point
   mapping, inclusions on the page, fulfilment-type choice. Often we are the
   root cause. Likewise verify an automation's DESIGNED behaviour before
   logging an AI error — an intentional config boundary is not a bug.

5. VERIFY EVERY GUEST CLAIM AT ITS SOURCE. Two steps, in order.
   FIRST list every factual claim the guest makes — in the review AND in what
   they told support. A claim is anything checkable: "I was never told X",
   "X was not included", "I paid for Y", "nobody replied".
   THEN route each claim to the one source that can prove or disprove it, per
   WHERE FACTS LIVE, and state what that source actually says.
   Worked example: guest claims "I was never told at booking that tickets would
   take 2 hours" → that is a disclosure claim about the experience page → check
   exp-page ticket_delivery / redemption instructions / important_instructions
   for a stated delivery window → `claim_accuracy` = "Inaccurate" with evidence
   text "Experience page states tickets are delivered within 2 hours" and
   source "exp-page"; or `claim_accuracy` = "Accurate" with evidence text
   "Experience page states no delivery window", whichever the data shows.
   The verdict follows the source, never what seems plausible. A claim whose
   source is unreachable is "Unknown".

6. SOP NEEDLE. Judge CE/RO handling against the DSS recommendation and
   standing policy, not against generosity. STANDING POLICY: an out-of-policy
   cancellation/modification request is DENIED first — a correct denial is
   never a CE miss. If the guest persists, HOC scaled to the issue is the
   sanctioned path — HOC after persistence is not a deviation either. Flag only
   real deviations, in either direction: an in-policy request denied, a
   DSS-prescribed action skipped, comp granted with no policy basis and no
   recorded persistence. Where DSS policy forks on "social media": every case
   here IS a public review, so the social-media variant always applies. If DSS
   is empty or match_score is 0, judge against standing policy and the scenario
   checklist only — never invent policy.

7. SUPPORT-FAILURE SUPERSEDES. If an external event occurred but CE or RO
   mishandled the contact, the root cause is the mishandling. What did the
   agent DO after acknowledging — escalated, or dropped?

8. SCOPE EVERY FINDING. One-off or pattern? Use the INSIGHTS counts (similar
   reviews, similar support contacts, completion rate) and state the window
   they cover in the issue's `pattern` field. A structural fix without sizing
   gets rejected. If the fault is ours, anything less than a full refund must
   be justified in one line.

9. POINT FORM, SHORT SENTENCES, FINDINGS ONLY. Every string is one short
   complete sentence — subject, verb, full stop. Target 8–16 words; 25 is the
   hard ceiling. "Selenium FF, no disclosure" is too clipped; "The page did not
   state the two-hour delivery window." is right.
   ONE IDEA PER STRING, NO SEMICOLONS. A semicolon means two entries welded
   together — split them, or drop the half the reader does not need. The same
   goes for "however", "although" and " — " used to bolt on a clause.
   A FINDING IS A FACT FROM THE DATA, NOT A JUDGEMENT. Write what the data
   shows, then the root cause. Never write advice, policy sermons, process
   proposals or verdict prose ("structurally impossible", "meets the
   threshold", "the workflow should"). Proposals live in the issue's `fix`
   field and in `area_of_improving`, nowhere else.
   Cut lead-ins ("It appears that", "It is worth noting") and adjectives that
   carry no fact. Never restate the review.
   SAY AN ABSENCE ONCE. When the case has no booking or no support contact,
   the root cause states it in full, once. Every other field notes only its own
   gap in six words or fewer: "No booking record.", "No guest contact found."
   Never explain again what the absence prevents. A one-line review must
   produce a one-page RCA, not the same absence restated in eight places.
   An absence note belongs in a scalar field only. Arrays stay empty — never
   emit a row whose summary says nothing was found.
   SCALE BY COUNT, NOT LENGTH: a complex case yields MORE entries, each still
   one short sentence.
   THIS RULE GOVERNS FINDINGS AND ANALYSIS STRINGS. Six fields follow their own
   lengths from the template instead: `claim` is copied verbatim at whatever
   length the guest wrote it; `issue` and `booking_logs.what` are labels with no
   full stop; and `stated_issue`, `root_cause` and `suggested_response` run to
   the sentence counts their template comments give. The `detail` fields carry a
   fuller account than a finding does.

10. BOOKING LOGS ARE CHRONOLOGICAL AND END WITH THE REVIEW. Include machinery
    (fulfilment runs, automated mails) where it explains the failure; a retry
    sequence stays as separate entries — collapsing three failures into one
    hides the root cause. `what` is a 3–8 word label with no full stop
    ("Selenium fulfilment attempt failed"); `detail` is one complete sentence,
    or null when the label says everything. Never return an empty list: when
    the systems gave you nothing, build the sequence from the guest's own
    account — they always narrate one — and end each such `detail` with
    "(guest's account, unverified)".

10b. A GUEST'S ACCOUNT HAS NO CLOCK, SO SAY SO IN `time`. A guest narrates an
    order of events, not timestamps. Returning null for every `time` on such a
    sequence renders a column of dashes, which reads as timestamps we failed to
    load rather than as timestamps that were never available — the same absence
    the dashboard shows for a broken lookup. So on any entry you built from the
    guest's account, `time` is the string "undated" rather than null.
    Two entries are exempt because the warehouse always knows them, and they
    give an undated sequence real bookends: the booking being made
    (<<BOOKING_DATE>>) and the visit (<<VISIT_DATE>>). Include both as the
    first and last dated entries whenever they are known, whatever else you
    have. `time` is null ONLY when you have a real event whose time genuinely
    is not recorded anywhere — not as a shorthand for "the guest did not say".

11. TAKEDOWN IS A FACTUAL TEST, NOT A SENTIMENT ONE. "Yes" only when the review
    is factually false or breaches platform policy. A review that is accurate,
    even partly, is "No" — however harsh its tone. "Untraceable" when no
    booking or contact record exists to check the claims against.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## OUTPUT FORMAT — return ONLY this JSON object, no prose before or after

{
  "stated_issue": "<2-3 sentences, 60 words MAX: the guest's problem in our words, for the top of the RCA>",
  "tldr": {
    "our_mistake": "<one sentence: what WE got wrong>",
    "our_fix": "<one sentence: what has been or will be done>"
  },
  "l1": "<the L1 category from the taxonomy>",
  "l2": "<the L2 category from the taxonomy, valid for that L1>",
  "sub_themes": ["<sub-theme from the L1::L2 framework, e.g. 'C. Ticket Delayed'>"],
  "scenarios": ["<scenario from SCENARIOS_ROUTED>"],
  "overlay_scenarios": ["<secondary scenario, not already in scenarios | omit if none>"],
  "what_went_wrong": {
    "guest_issues": [
      {
        "issue": "<one-line title, max 15 words, no trailing period>",
        "claim": "<the guest's VERBATIM words from the review, quoted exactly | null>",
        "claim_accuracy": "<Accurate | Partly accurate | Inaccurate | Unknown>",
        "claim_accuracy_note": "<one sentence of reasoning for that verdict | null>",
        "owner": "<Content | CE | SP | RO | Product | Biz | Ops>",
        "root_cause": "<the failing step, 1-2 sentences>",
        "operational_failure": "<what a person or system did wrong on THIS issue | null>",
        "sop_gap": "<the missing or deficient process step for THIS issue | null>",
        "pattern": "<recurrence evidence for THIS issue, with counts and window | null>",
        "fix": "<'Team: action (owner: Team)' for THIS issue | null>",
        "evidence": [
          {
            "text": "<one sentence stating the finding, no source prefix, no URL>",
            "source": "<booking | bms | zendesk | insights | dss | exp-page>",
            "ref": "<record URL or ZD-xxxxx | null>"
          }
        ]
      }
    ]
  },
  "issue_specific_answers": [
    {
      "question": "<the question from ISSUE_QUESTIONS, verbatim>",
      "verdict": "<Yes | No | Unknown>",
      "evidence": "<the fact that settles it, one or two sentences | null>",
      "source": "<booking | bms | zendesk | insights | dss | exp-page | null>",
      "ref": "<record URL or ZD-xxxxx | null>"
    }
  ],
  "sop_compliance": {
    "verdict": "<followed | deviated | unknown>",
    "expected": "<what the SOP required>",
    "actual": "<what actually happened>",
    "detail": "<qualifier or exception note | null>",
    "zd_ref": "<ZD-xxxxx | null>"
  },
  "support_interaction_notes": [
    {
      "zd_ref": "<ZD-xxxxx — the ticket this note is about; this is the join key | null>",
      "summary": "<one line, what happened in this contact>",
      "detail": "<the fuller account, quoting the guest and the agent | null>",
      "ce_miss": "<what CE should have done differently | null>"
    }
  ],
  "sp_interaction_notes": {
    "raised": "<Yes | No | N/A>",
    "reason": "<why not, when raised is No or N/A: e.g. 'vendor is not a partnered SP' | null>",
    "records": [
      { "zd_ref": "<ZD-xxxxx — the join key | null>", "summary": "<what was raised and what came back>" }
    ]
  },
  "booking_logs": [
    { "time": "<DD Mon HH:MM | null>", "what": "<the event>", "detail": "<the specifics | null>" }
  ],
  "flags": [
    { "team": "<CE | RO | SP | CONTENT | PRODUCT | BIZ | TECH | OTHER>",
      "flag": "<one line: what went wrong that someone must act on>",
      "evidence": "<the fact that proves it>",
      "zd_ref": "<ZD-xxxxx | null>" }
  ],
  "area_of_improving": ["<one improvement per array element>"],
  "resolution": "<what the guest actually got: refund / comp / explanation, with amounts>",
  "suggested_response": "<the reply to the guest, 4-6 SHORT SENTENCES (~120 words): apologise, state what went wrong in plain words, state the remedy with its reference, close warmly. No internal jargon, no BID, no team names>",
  "takedown": { "verdict": "<Yes | No | Untraceable>" },
  "dss": {
    "prescribes": "<what the matched DSS row prescribes for this scenario>",
    "ref": "<DSS row URL | null>"
  }
}

## OUTPUT RULES — these are hard constraints, not preferences

1. Return ONLY the JSON object. No markdown fences, no commentary, no trailing explanation.
2. Every field in the template must be present. Use null for unknown or absent — never the
   strings "Unknown", "N/A", "TBD", "-", "?", "none" or an empty string in any field except
   where an enum explicitly lists that value.
3. `claim_accuracy` MUST be exactly one of: Accurate, Partly accurate, Inaccurate, Unknown.
   Nothing else, no punctuation, no trailing explanation. Put your reasoning in
   `claim_accuracy_note`. Do NOT write "Partially True — booking status shows…" in the verdict.
4. `claim` is the guest's own words copied from the review, inside no quote marks (the UI adds
   them). Never paraphrase. If the review does not state this issue in the guest's words, use null.
5. Every analytical statement attaches to the issue it explains. `operational_failure`,
   `sop_gap`, `pattern` and `fix` are fields ON each guest issue. Do NOT emit document-level
   `what_happened`, `root_causes`, `operational_failure`, `sop_gap`, `pattern` or `fixes` lists.
   `owner` names the internal team that must ACT on this issue. When the claim is Inaccurate, or
   when no internal team is at fault, `owner` is null — never "Guest", "Customer", "None" or
   "N/A". A guest cannot be assigned work, so naming one as owner puts a party who will never
   see this RCA on the hook for the fix.
6. `evidence[].source` and `.ref` are structured fields. The `text` must contain no `[booking]`
   or `[insights]` prefix and no URL — put the identifier in `ref` and the origin in `source`.
6b. When a disclosure claim is in play — the guest says they were not told something, or were
   told the wrong thing — check EVERY piece of guest-facing copy in the data, not just the first
   one that settles it: the experience page, the booking-in-progress email, the confirmation
   email and its Know Before You Go block. Our own copy contradicting itself is a finding in its
   own right, and a bigger one than an omission: "the page does not say" is a gap, while "the
   page says two hours and the confirmation says one day before" is two teams disagreeing in
   front of the guest. Raise it as its own CONTENT flag with both statements quoted.
7. No bullet characters (•, -, –, *) or leading numbering ("1.", "a)") inside any string.
   Each array element is exactly one point, one line.
8. All timestamps are IST, formatted `DD Mon HH:MM` (e.g. `22 Jul 15:41`), or a bare `DD Mon`
   for a date-only event, or null. Never "Unknown" and never an ISO string.
9. One guest issue per distinct complaint in the review. If the guest raises three things,
   return three objects — do not merge them, and do not invent a second issue when there is one.
   Splitting a cause from its consequence is inventing one: "we did not disclose the delivery
   window" and "the delivery window clashed with their schedule" are one complaint, and the
   consequence belongs in that issue's `root_cause`, not in an issue of its own.
   Two checks that catch a bad split, both of which you can run on your own draft before
   returning it. (a) An issue's `operational_failure` must describe conduct by the team named in
   its `owner`. If you have written owner "RO" and an operational_failure about what CE did, the
   issue belongs to CE — or it is the same issue as one you have already written for CE, and
   should be merged into it. (b) If an issue's `root_cause` restates another issue's finding,
   that issue is the other one's consequence. Merge it.
   Every entry must trace to something the guest SAID OR IMPLIED. Our own process gaps are not
   guest issues however serious — an out-of-policy refund, a missed SOP step, a DSS path not
   followed go to `flags` and `sop_compliance`. `claim` is null only where the review implies
   the issue without words, or on a rule 13 routed-scenario coverage row; a `guest_issues` entry
   with no claim and no routed scenario behind it renders as a numbered guest complaint with an
   empty Claim block, and leadership reads it as something the guest said. They did not.
   Do not repeat in `guest_issues` anything you have already raised in `flags`.
10. `flags` contains failures only — things a named team must act on. An empty array means
    everything was checked and nothing needed raising; return `[]`, not a placeholder entry.
11. If a section genuinely has nothing (no SP contact, no support contact), return an empty
    array. Do NOT fabricate a row whose summary says nothing was found. The REVIEW ITSELF is
    never a support contact: it is the artefact being analysed, not a channel the guest reached
    us on. Never emit a `support_interaction_notes` row for it, and never write "Trustpilot",
    "review" or "public review" as a `channel` — a phantom contact makes the contact count
    permanently one too high, and it reads as if someone handled the guest when nobody did.
12. `issue_specific_answers`: one object per question in ISSUE_QUESTIONS, in the order given,
    question text copied verbatim. `verdict` MUST be exactly Yes, No or Unknown — the reasoning
    and the numbers go in `evidence`. Do NOT prefix the evidence with the verdict, and do NOT
    answer with a sentence in the verdict field ("28 minutes (…)" is an evidence value, not a
    verdict). If a question cannot be answered from the data, verdict is Unknown with the
    reason in `evidence` — never omit the question.
13. Every scenario in SCENARIOS_ROUTED must be covered by at least one guest issue: its root
    cause and fix live on that issue. Do NOT emit a separate per-scenario block, and do not
    drop a routed scenario — if a routed scenario is not supported by the review or the data,
    return a guest issue for it with `claim_accuracy: "Inaccurate"` or `"Unknown"` and say why
    in `claim_accuracy_note`.
14. `dss.prescribes` states what the matched DSS row prescribes for this scenario, in its own
    words — it is reference data, not your analysis, so do not add whether we complied (that is
    `sop_compliance`) and do not restate the row's L1/L2/sub-theme (the UI derives that from the
    classification). Evidence drawn from the DSS sheet uses `"source": "dss"` — never
    `"insights"` (a DSS needle is a playbook lookup, not a warehouse aggregate).
15. `l1`, `l2` and `sub_themes` must come from the taxonomy verbatim (including any letter
    prefix, e.g. `"C. Ticket Delayed"`). Never invent a category, never abbreviate one, and
    never leave them empty — if the review is unclassifiable, use the taxonomy's own catch-all.
    `overlay_scenarios` must not repeat anything already in `scenarios`.
16. `suggested_response` is guest-facing: no BID, no ticket ids, no internal team or system
    names (Selenium, Minded AI, DSS, BMS), no policy jargon. State the remedy concretely with
    its reference if one exists. Write it in the guest's language where the review is not in
    English; the English draft goes in `suggested_response` and the translation is a separate
    step.
17. `resolution` records what the guest ACTUALLY received, not what was recommended. If nothing
    has been given yet, say so plainly ("Nothing offered yet") rather than describing an intent.
17b. Two hard length ceilings, because both fields are read by someone outside this system.
    `suggested_response` is 4-6 SHORT SENTENCES, about 120 words — count the sentences, not the
    words, and stop at six. It goes on a public review page, and 200 words under a one-star
    review reads as defending ourselves rather than apologising. The approved reply voice
    examples run to about 90 words; match their length as well as their register. `stated_issue` is
    2-3 sentences, 60 words MAX — it is the one-glance summary at the top of the RCA, not a
    retelling of the review. Say less and stop.
18. `support_interaction_notes` and `sp_interaction_notes` are your INTERPRETATION of contacts
    the system already has as facts. The rows the UI renders come from Zendesk: their time,
    channel and ticket id are established, are not yours to restate, and have no field here to
    put them in. Your job is `summary`, `detail` and `ce_miss`, joined to a contact by `zd_ref`.
    A note with `zd_ref: null` is a contact you can see in the review or the raw ticket bodies
    that has NO Zendesk ticket behind it; it renders marked unverified, so use it for a real gap
    (the guest says they phoned and no ticket exists), never to restate one already there.
    `sp_interaction_notes.reason` says why escalation did not happen when `raised` is No or N/A —
    a blocked escalation (non-partnered vendor, opted-out contact) is a FACT about this booking,
    not a miss, and with no reason stated "N/A" is indistinguishable from a section you skipped.
19. `suggested_response` follows the voice of the APPROVED REPLY VOICE examples — their register,
    warmth and sentence rhythm — and NONE of their content. Never copy a sentence from them,
    never carry over a remedy they mention, and never use one as a template to fill in. The
    facts of this reply come only from this case's evidence."""

# The name the filler and every existing import still use.
RCA_V3_TEMPLATE = RCA_V4_TEMPLATE

# Stamped onto every draft the pipeline writes. Without it a v3 row and a v4
# row are told apart only by guessing from their shape, which is how a
# pre-deploy draft got read as a v4 checkpoint: every enum violation in it was
# a v3 artefact, and nothing on the row said so.
#
# Content-addressed, because "rca_v4" was not enough. Two rows written hours
# apart carried the same stamp across a prompt change that added rules, so
# there was no way to tell whether a finding meant "the new clause did not
# work" or "this row predates the clause" - the same ambiguity one level down.
# The suffix changes whenever the template text changes, so the question is
# answerable exactly rather than by reading timestamps against deploy times.
def _bookend(bk: dict, *keys) -> str:
    """A date the model can put in `time`, or a phrase saying we do not have it.

    Rule 10b uses these as the two dated bookends of an otherwise undated
    sequence. Returning "" for a missing one would silently drop the bookend
    and leave the rule asking for something that is not there; naming the
    absence keeps the model from inventing a date to satisfy it.
    """
    for k in keys:
        v = str((bk or {}).get(k) or "").strip()
        if v:
            return _fmt_bookend_time(v) or v
    return "not recorded — omit this bookend"


def _prompt_digest(text: str) -> str:
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]


RCA_PROMPT_FAMILY  = "rca_v4"
RCA_PROMPT_VERSION = f"{RCA_PROMPT_FAMILY}+{_prompt_digest(RCA_V4_TEMPLATE)}"


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


def _fmt_bookend_time(dt_str: str) -> str:
    """Bookend timestamp in the same shape zendesk._to_ist gives real events."""
    # The review's publish date reaches this prompt as a bare '%Y-%m-%d' (built by
    # pipeline.py) and the booking date as 'DD Mon YYYY'. Interpolating either one
    # straight into the bookend example handed the model timestamps no real event
    # ever carries, so the bookends rendered and sorted as a special case - the
    # client ended up hand-patching ISO strings back into 'DD Mon' to cope.
    ist = _fmt_date_ist(dt_str)
    if ist.endswith(" IST"):
        return ist
    # Real events carry no year, so a date-only source degrades to 'DD Mon'.
    # Matched on shape rather than on word count: a three-word string is not
    # necessarily 'DD Mon YYYY', and blindly keeping the first two words turned
    # an unparseable value into a plausible-looking date fragment - 'not a
    # date' became 'not a', which would have sat in the timeline as if it were
    # a timestamp.
    parts = ist.split()
    if (len(parts) == 3 and parts[0].isdigit() and len(parts[0]) <= 2
            and parts[2].isdigit() and len(parts[2]) == 4):
        return " ".join(parts[:2])
    return ist


def zendesk_timeline_shape_prompt(
    booking: dict,
    review_body: str,
    review_pub_date: str,
    raw_events: list,
) -> str:
    """
    Shape raw Zendesk events into the timeline the dashboard renders.

    The model writes two things: a short label and one factual sentence. It
    does not decide facts. time, thread, actor, ticket_id and is_internal are
    recorded by Zendesk, classified in zendesk.py, and copied through - because
    a model asked to carry a fact will eventually drop it, and a fact it
    "corrected" is indistinguishable from one that was right.

    What this replaced, and why, each verified against booking 32908218:

    - It asked the model to infer the channel from the raw body. Zendesk
      records the channel; guessing it put WhatsApp on the email thread.
    - It listed patterns of internal noise for the model to drop by judgement.
      That is deterministic and now happens in zendesk.py, where it is
      testable, and machinery is MARKED rather than dropped so a
      misclassification can be found instead of vanishing.
    - Its bookend example interpolated a raw ISO date while a rule four lines
      later banned raw ISO dates.
    - Nothing bound a label to an actor, so system mail was labelled as the
      guest speaking.
    - Nothing stopped an event taking its label from the event beside it: the
      booking dump came back labelled as the email one second away, and the
      fulfilment attempt disappeared.
    - On a retry sequence it collapsed three failures and a success into ONE
      row labelled "Tickets sent". A vendor that failed three times read as a
      clean delivery. tools/try_timeline_prompt.py --fixture retries is that
      case; it now returns four rows.
    """
    bk = booking or {}
    booking_date_fmt = _fmt_bookend_time(bk.get("date_of_booking") or bk.get("creationDate") or "")
    review_date_fmt  = _fmt_bookend_time(review_pub_date) if review_pub_date else "unknown"
    visit_date_raw   = bk.get("visitDate") or bk.get("date_of_visit") or ""
    visit_date_fmt   = _fmt_date_ist(visit_date_raw) if visit_date_raw else "the visit date"

    booking_summary = {k: v for k, v in bk.items()
                       if k not in ("_match", "timeline_raw")}
    events_json = json.dumps(raw_events or [], indent=2)
    booking_json = json.dumps(booking_summary, indent=2)

    return f"""You are shaping raw Zendesk support events into a clean, human-readable
timeline for an internal ORM dashboard. Headout CX analysts will read this - it must
be factual and concise.

=== BOOKING METADATA ===
{booking_json}

=== REVIEW ===
Published: {review_pub_date}
Body: {review_body}

=== RAW EVENTS (idx = sequential order) ===
{events_json}

=== WHAT THIS TIMELINE IS ===
A clear, human story of the guest's journey - the booking, any contact with
support, what we did in response, and how it ended. A CX analyst should read it
top-to-bottom and understand: did the guest reach out, HOW, WHY, WHAT we did,
and whether the booking was fulfilled or resolved.

=== WHAT YOU DECIDE, AND WHAT YOU MUST NOT TOUCH ===
You are writing two things and nothing else: a short LABEL and a one-sentence
SUMMARY for each event, plus which events collapse together.

These fields are facts recorded by Zendesk. Copy each one through EXACTLY as
given. Never infer, correct, reformat or fill one in:
    time, thread, actor, ticket_id, is_internal
If a value looks wrong to you, copy it anyway. A wrong value that survives is
findable; one you quietly corrected is not.

=== INSTRUCTIONS ===
1. BOOKENDS - inject exactly two, not present in raw_events. They frame the
   timeline and are system markers, NOT guest or agent speech: copy their
   idx_range, time, thread, actor and label EXACTLY as written. Never a person
   actor, never a conversation thread, never a name or a quote.
   - FIRST - Booking created:
     {{"idx_range": [], "time": "{booking_date_fmt}",
       "thread": "booking", "actor": "creation",
       "label": "Booking created",
       "summary": "<WHAT the guest booked - variant / pax / options selected, and
       notably any upsell or add-on NOT selected at checkout. From the booking
       metadata. Do NOT write the full experience name.>", "keep": true}}
   - LAST - Review posted:
     {{"idx_range": [], "time": "{review_date_fmt}",
       "thread": "review", "actor": "review",
       "label": "Review posted",
       "summary": "Negative Trustpilot review posted, BID referenced.", "keep": true}}

2. KEEP EVERY EVENT. keep: false only for an event with no readable content at
   all - an empty body, a bare signature, a logo. Do NOT drop machinery:
   is_internal already marks it and the dashboard hides it behind a toggle that
   says how many it hid. An event you drop cannot be recovered or counted.

3. COLLAPSE consecutive events describing ONE action at one moment; list every
   collapsed idx in idx_range. Collapse only within the same thread and the
   same actor - merging a guest message into a system row destroys both. No
   "(xN)" in the label.

4. LABELS - short and plain, from this vocabulary:
   "Booking created", "Tickets sent", "Guest reached out", "Guest reply",
   "CE response", "SP response", "Refund issued", "Booking cancelled",
   "Escalated to SP", "Review posted".
   THE LABEL MUST MATCH THE ACTOR. This is not a style preference - a label
   naming someone who did not act is a false statement about a person, and it
   is the one error here that can end up quoted back to a customer.
     actor "guest"   -> and ONLY then: "Guest reached out" (first contact) or
                        "Guest reply" (any later message). Never write that the
                        guest contacted, asked, replied or complained unless
                        this event IS the guest's own words.
     actor "co"      -> "CE response"
     actor "sp"      -> "SP response"
     actor "system" / "ai" -> the machine action AND ITS OUTCOME: "Fulfilment
                        run failed", "Tickets sent", "Booking-in-progress email
                        sent", "Credentials generated". Name the specific
                        machine that ran: a fulfilment attempt is "Fulfilment
                        run ...", never the name of the email beside it.
                        internal_reason "booking-info" is NOT a run. It is the
                        booking dump Zendesk posts onto the ticket - pax,
                        price, vendor, instructions. Label it "Booking details
                        posted" and summarise the facts in it. Do not write
                        that anything ran or was attempted: naming a
                        fulfilment attempt that never happened invents the
                        event an RCA then goes looking for.
                        Say what happened, not what was tried -
                        "Fulfilment run attempted" leaves the reader to find
                        out whether it worked, and whether it worked is the
                        whole reason the row is here.
                        An automated email ABOUT the guest is a system event,
                        not the guest speaking.
     actor "system" on thread "chat" -> a chat TRANSCRIPT: ONE comment holding
                        the whole conversation, posted by Zendesk rather than
                        by either party, which is why its actor is system.
                        Label it "Guest chat". Do NOT label it as a transcript
                        or a log - the log is the container, the conversation
                        is the event, and calling it bookkeeping buries the
                        only record of what the guest said.
                        The summary carries what the guest raised and what they
                        were told, in that order. Attribute inside the summary
                        ("Guest asked ... ; agent said ...") - that is accurate
                        about a transcript in a way the actor field cannot be.
                        Rule 5 style applies: three phrases at most - what
                        the guest raised; what they were told; how it ended.
                        The rest of the transcript is one click away on the
                        ticket link, which is what that link is for.
   LABEL EACH EVENT FROM ITS OWN BODY, never from the event beside it. On
   booking 32908218 the Selenium fulfilment blob and the booking-in-progress
   email - two different things one second apart - both came back
   "Booking-in-progress email sent". The fulfilment attempt took the label of
   the mail it sat next to and disappeared, and that attempt is often the
   whole root cause.
   Repeated labels are NOT automatically wrong. Three fulfilment retries are
   three events that each say "Fulfilment run failed", and forcing them to
   differ would invent a distinction the data does not have. Before you write
   a label, ask of the BODIES, not of the labels:
     - Same action recorded more than once at one moment? -> ONE event.
       Collapse under rule 3 and list every idx.
     - Same KIND of action happening again at a different time? -> SEPARATE
       events, and the same label on both is correct. Let the summary carry
       what differed - the attempt number, the outcome, what changed.
     - Different actions? -> different labels, each from its own body.
   No ticket IDs, no "[ZD-xxxxx]", no "(xN)".

5. SUMMARIES - NOT sentences. 2-3 telegraphic phrases separated by "; ",
   each phrase one fact. Hard limit 100 characters total. Drop articles,
   subjects and connective prose; keep numbers, names, dates and outcomes:
     "1 Adult + 1 Reduced; PLN 73.73; no add-on selected"
     "no ticket URLs; vendor page timed out"
     "ref 1022394558263; valid to 22 Jul 2027"
     "guest wanted tickets now; told 2h delay; left unresolved"
   The outcome phrase is mandatory - how it ended is the one thing a CX
   analyst cannot infer from the label, so it must never be the phrase that
   gets dropped to make room for detail.
   Do not restate the label - the label already says what the event is; the
   phrases carry only what the label cannot.
   Keep the specifics that let someone verify: amounts, pax, reference
   numbers, dates. Everything else goes.
   Say only what the event evidences - never supply a motive the body does
   not state. Strip HTML and signatures. Never quote raw JSON. Never adopt
   the guest's emotional wording.

6. ORDER - Booking created first, events as given, Review posted last. The
   input is already in order; do not re-sort it.

Return ONLY valid JSON - a list of shaped event objects, nothing else:
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

def reply_translation_prompt(text: str, lang: str) -> str:
    """Outgoing guest reply, English -> the language the guest wrote in."""
    return f"""Translate this customer-service reply from English into {lang}.

Rules:
- Keep the tone: warm, plain, not formal-stiff. Match how a real support
  agent writes in {lang}, not a literal word-for-word rendering.
- Booking references, ticket ids, amounts, dates and proper nouns stay
  EXACTLY as written.
- Keep the paragraph breaks.
- Return ONLY the translated reply. No preamble, no notes, no quotes around
  it.

REPLY:
{text}"""
