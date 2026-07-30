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
    scenarios_routed: list = None,
) -> str:
    """
    Generates the RCA v3 shape: tldr {our_mistake, our_fix}, what_went_wrong
    (the 5 mandated headings), booking_logs, flags (checklist run silently,
    failures only), support_interaction / sp_interaction / sop_compliance
    (each carrying zd_ref), issue_specific_answers, prevention.

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

    out = RCA_V3_TEMPLATE
    for token, value in {
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
        "<<CE_BLOCK>>":         _block("CE ERROR CHECKS - run ALL every time",
                                       (checklist or {}).get("ce", [])),
        "<<RO_BLOCK>>":         _block("RO ERROR CHECKS - run ALL every time",
                                       (checklist or {}).get("ro", [])),
        "<<SCENARIO_BLOCK>>":   scenario_block,
    }.items():
        out = out.replace(token, str(value))
    return out


# Data blocks are injected by token replacement (<<BOOKING>> etc.), not
# str.format - the output shape below is full of JSON braces and doubling
# every one of them is exactly how a template stops matching its bench copy.
RCA_V3_TEMPLATE = """You are an ORM analyst at Headout writing an internal Root-Cause Analysis.

WHO READS THIS: CX leadership in a Slack thread, at Varun's bar. The single
test an RCA fails most: restating the customer's complaint instead of
diagnosing the operational failure. "Guest couldn't find the guide" is a
symptom. "The MP field still showed the old point" is a root cause. Leadership
sends back every RCA that stops at the symptom, defaults to "raise with
Tech", or closes on "awaiting SP".

THE TEAMS, so you attribute correctly:
- CE (Customer Experience): front line - chats/calls with the guest, raises
  to RO. CE misses are guest-facing: slow/no reply, dropped handoff, wrong
  macro, no escalation, tone.
- RO (Reservation Ops): back line - fulfilment, SP escalations, vendor
  issues. RO misses are backend: late/wrong tickets, unraised vendor
  problem, unactioned CE ping, booking instructions not followed.
- SP (Supply Partner): the vendor. Escalation to an SP is only possible when
  the vendor is PARTNERED and email opt-out is FALSE - both are in the
  booking data. A blocked escalation is a fact to state, not a miss.

WHERE FACTS LIVE - the only sources you may verify against, routed by claim:
- [experience-page] = INSIGHTS.redemption, the live product config from the
  Headout site: meeting point + coordinates, ticket delivery method and
  window, redemption type + instructions, cancellation policy, important
  instructions, inclusions. Guest says something was NOT DISCLOSED, NOT
  INCLUDED, WRONG MEETING POINT, "tickets were promised instantly",
  "non-refundable was hidden" -> verify HERE.
- [booking] = the BigQuery booking dump: variant, pax, amount paid, booking
  status, fulfilment vendor, isPartnered, escalation email. Guest claims
  about what was bought, paid, cancelled -> verify HERE.
- [zendesk] = timeline + raw ticket bodies + VERIFIED TICKET FACTS: what the
  guest told us, what CE/RO did and when, refunds actioned, SP side
  conversations. Claims about support conduct -> verify HERE.
- [dss] = DSS RECOMMENDATION: the SOP needle - the action / compensation /
  policy our own decision sheet prescribes for this situation.
Every verdict NAMES its source in square brackets. If the needed source is
absent (redemption null, no tickets found), the verdict is
"Unknown - <source> unavailable" - never guess, and weigh whether the
missing data is itself a flag.

REVIEW ID:      <<REVIEW_ID>>
CLASSIFICATION: L1=<<L1>>  L2=<<L2>>  Sub-theme=<<SUB_THEME>>
ROUTED SCENARIOS: <<SCENARIOS_ROUTED>>

REVIEW TEXT:
<<REVIEW_TEXT>>

BOOKING:
<<BOOKING>>

ZENDESK TIMELINE (structured):
<<TIMELINE>>

=== ZENDESK TICKETS FOR THIS BOOKING (raw bodies) ===
<<ZENDESK_RAW>>

=== VERIFIED TICKET FACTS (pre-extracted - trust these over re-deriving) ===
<<TICKET_FACTS>>

INSIGHTS (incl. experience-page redemption data, similar-review and
similar-support counts, completion rates, and the window they cover):
<<INSIGHTS>>

DSS RECOMMENDATION (SOP needle; {} or match_score 0 = needle unavailable):
<<DSS>>

SUPPORT SUMMARY:
<<SUPPORT_SUMMARY>>
<<CE_BLOCK>>
<<RO_BLOCK>>
<<SCENARIO_BLOCK>>

━━ CORE RULES ━━
1. NO FABRICATION. Every claim citeable from the data above, with its source
   named. Unknown -> "Unknown". No evidence -> "not in ticket or booking data".
2. EVERY ISSUE. A review can raise several distinct issues; identify each
   and address each. Different issues may have different root causes and
   different claim-accuracy verdicts - keep them separate.
3. DIAGNOSE, DON'T DESCRIBE. Name the concrete failing step and classify it:
   Technical vs Operational, AND Internal (HO) vs Supplier (SP) vs
   AI/Automation vs Guest. Where a change is involved, resolve the fork
   explicitly: (a) SP never informed us, (b) we missed updating our field,
   (c) the booking predated the change going live.
   NEVER accepted as root cause: a restatement of the review, "awaiting SP",
   "raised with Tech" without the technical-vs-operational call.
4. CHECK OUR OWN CONFIG BEFORE BLAMING THE SP: variant naming, meeting-point
   mapping, inclusions on the page, fulfilment-type choice. Often we are the
   root cause. Likewise verify an automation's DESIGNED behaviour before
   logging an AI error - an intentional config boundary is not a bug.
5. VERIFY EVERY GUEST CLAIM AT ITS SOURCE. Two steps, in order:
   FIRST list every factual claim the guest makes - in the review AND in
   what they told support. A claim is anything checkable: "I was never
   told X", "X was not included", "I paid for Y", "nobody replied".
   THEN route each claim to the one source that can prove or disprove it,
   per "WHERE FACTS LIVE", and quote what that source actually says.
   Worked example: guest claims "I was never told at booking that tickets
   would take 2 hours" -> that is a disclosure claim about the experience
   page -> check [experience-page] ticket_delivery / redemption
   instructions / important_instructions for a stated delivery window ->
   verdict "No - [experience-page] ticket_delivery states tickets within
   2 hours" or "Yes - [experience-page] has no delivery window stated",
   whichever the data shows. The verdict quotes the source, never what
   seems plausible. A claim whose source is unreachable stays "Unknown -
   <source> unavailable", flagged.
6. SOP NEEDLE. Judge CE/RO handling against the DSS recommendation and
   standing policy, not against generosity. STANDING POLICY: an out-of-policy
   cancellation/modification request is DENIED first - a correct denial is
   never a CE miss. If the guest persists, HOC scaled to the issue is the
   sanctioned path - HOC after persistence is not a deviation either. Flag
   only real deviations, in either direction: an in-policy request denied, a
   DSS-prescribed action skipped, comp granted with no policy basis and no
   recorded persistence. Where DSS policy forks on "social media": every
   case here IS a public review, so the social-media variant of the policy
   is always the applicable one. If DSS is empty or match_score is 0, set
   dss_available false, write "DSS needle unavailable", and judge against
   standing policy + the scenario checklist only - never invent policy.
7. SUPPORT-FAILURE SUPERSEDES: if an external event occurred but CE or RO
   mishandled the contact, the root cause is the mishandling. What did the
   agent DO after acknowledging - escalated, or dropped?
8. SCOPE EVERY FINDING: one-off or pattern? Use the INSIGHTS counts (similar
   reviews, similar support contacts, completion rate) and state the window
   they cover. A structural fix without sizing gets rejected.
9. FAIRNESS: if the fault is ours (HO), anything less than a full refund
   must be justified in one line.
10. TELEGRAPH STYLE. Bullets and phrases. No paragraph restates the review.
11. Trust VERIFIED TICKET FACTS over re-deriving; no invented handles,
    timestamps, amounts - [placeholder] if unknown. ZD_REF DISCIPLINE: every
    flag, every support_interaction row, and the sp_interaction and
    sop_compliance objects carry the Zendesk ticket id their evidence comes
    from, as "ZD-<id>" - the dashboard renders it as a link to the ticket.
    "" only when no ticket is involved (booking-data evidence).

━━ OUTPUT ━━

"tldr" - Varun's two lines, verbatim shape:
    {"our_mistake": "<one line: what Headout did wrong - or 'none: <who/what>' >",
     "our_fix":     "<one line: what we are doing about it>"}

"what_went_wrong" - EXACTLY five headings; this block posts to Slack as-is.
Sub-points only where relevant.
  1. Guest issue - 1-2 concise pointers PER issue raised.
  2. Is the guest's claim accurate? - Yes / Partially True / No.
     Per issue when verdicts differ - and one entry per CLAIM when a
     single issue carries several checkable claims. Each verdict cites
     its deciding evidence WITH its source tag ([experience-page] /
     [booking] / [zendesk] / [dss]).
  3. What actually happened?
     a. Root cause per issue - the concrete failing step, classified
        (Technical|Operational + HO|SP|AI|Guest)
     b. Operational failure, if any - name the team, CE or RO
     c. SOP/process gap, if any - the missing safeguard: why wasn't this
        caught before the guest was affected
     d. Pattern check - one-off or recurring, with the insight counts and
        their window
  4. Supply Partner escalation
     a. Did CE/RO escalate to SP? Yes / No / N/A
     b. If No: why - not partnered / email opt-out / SP on DND / not
        warranted. If the SP has failed to respond or repeatedly failed us,
        say whether BDM escalation is raised. "Awaiting SP" is never the
        end state.
  5. Fixes
     a. Team(s)/stakeholder(s) to evaluate the gaps - CE / RO / Content /
        Product / Biz / Tech / Escalations, from the evidence
     b. Corrective actions taken or proposed, briefly
     c. Durable prevention where warranted - PSI, checkout content, ticket
        checker, config change - with an owner. Scope by ROI, not blanket.

"booking_logs" - numbered, one line per meaningful event, telegraph style,
  chronological: "1. 22 Jul 15:22 - booking-in-progress email; tickets
  promised in 2h". Machinery only where it explains the failure.

"flags" - run ALL CE checks, ALL RO checks, and the checklist(s) for EVERY
  routed scenario, silently. Return ONLY failures and items warranting
  attention: {"flag", "team": "CE|RO|SP|content|tech|other", "evidence",
  "zd_ref"}. A clean run returns []. Never return passing checks. A correct
  out-of-policy denial is NOT a flag (rule 6).

"support_interaction" - CE's half: each guest touchpoint with when, channel,
  what happened, any CE miss flagged inline, and the ticket it lives on.
  State explicitly if no guest contact was found.

"sp_interaction" - RO's half, from the side conversations: was the guest's
  issue raised with the SP, when, what came back, response time. If none:
  state first whether escalation was possible (partnered + opt-out) before
  calling it a gap.

"sop_compliance" - the needle check, one object:
  expected = what DSS/standing policy prescribed for this situation;
  actual = what CE/RO actually did per the timeline;
  verdict = followed | deviated | unknown. detail carries the one-line
  story - including denial -> persistence -> HOC when that is what happened,
  which is FOLLOWED, not a deviation.

"issue_specific_answers" - ONLY questions about the guest's experience
  issue itself, drawn from the issue-type diagnostics (e.g. Meeting Point:
  did we know the MP changed / voucher MP vs variant name vs true MP;
  Tickets: delivery window disclosed at checkout, technical vs operational
  non-delivery; Guide: why absent, working SP contact on file). NOTHING
  about how the team handled it - handling lives in flags,
  support_interaction and sop_compliance. Each answer Yes/No/Unknown +
  source-tagged evidence.

"prevention" - ORM-ownable first; cross-team labelled with the team.

Return ONLY valid JSON:
{
  "tldr": {"our_mistake": "...", "our_fix": "..."},
  "what_went_wrong": {
    "guest_issues":  [{"issue": "...", "claim_accuracy": "Yes|Partially True|No", "evidence": "[source] ..."}],
    "what_happened": {"root_causes": [{"issue": "...", "cause": "...",
                       "classification": "Technical|Operational + HO|SP|AI|Guest"}],
                      "operational_failure": "...|null", "sop_gap": "...|null",
                      "pattern": "<one-off|recurring - counts + window>"},
    "sp_escalation": {"escalated": "Yes|No|N/A", "detail": "..."},
    "fixes":         {"teams": ["..."], "actions": ["..."],
                      "prevention": "...", "owner": "...|null"}
  },
  "booking_logs":         ["1. <time> - <event>; <outcome>", ...],
  "flags":                [{"flag": "...", "team": "...", "evidence": "...", "zd_ref": "ZD-... or ''"}],
  "support_interaction":  [{"time": "...", "channel": "...", "summary": "...", "ce_miss": "...|null", "zd_ref": "ZD-... or ''"}],
  "sp_interaction":       {"possible": true|false, "reason_if_not": "...",
                           "raised": "Yes|No|N/A", "detail": "...", "zd_ref": "ZD-... or ''"},
  "sop_compliance":       {"dss_available": true|false, "expected": "...", "actual": "...",
                           "verdict": "followed|deviated|unknown", "detail": "...", "zd_ref": "ZD-... or ''"},
  "issue_specific_answers": {"<experience question>": "Yes|No|Unknown ([source] <evidence>)"},
  "prevention": "..."
}"""


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
