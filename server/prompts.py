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
import os
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

A REMEDY REFUSED IS NOT ITS OWN ISSUE. When support denied a refund, a
reschedule or a goodwill gesture that was owed BECAUSE OF ANOTHER FAILURE OF
OURS (not an external event — a refusal after a flight cancellation or a storm
stays Customer Support Issues, because nothing of ours failed first),
classify the FAILURE, not the denial. The denial is what the guest writes
about — it is the last thing that happened to them — but it is the
consequence, and the L2 must name the cause. Customer Support Issues is for a
support failure that IS the complaint: rudeness, no reply, a reply that never
came, factually wrong information given by support. It is not for the refusal
of a remedy another failure created the need for.
WORKED EXAMPLE (Zoomarine): the guest was charged for a child ticket that
should have been free, and the refund they asked for was then denied. Two
things happened; only one of them is the issue. The charge exists because the
free-child pax type is missing from what we published and sold, so
  → L1 = "Operations Issue" / L2 = "Content - Instructions not clear / Misleading Info".
NOT Customer Support Issues, which describes only the second half and sends the
case to the team that refused the refund instead of the team whose page caused
the charge.
TEST: remove the denial from the review. If a complaint remains, that
complaint is the L2. If nothing remains — the guest's only grievance is how
support treated them — then it is Customer Support Issues.
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
  BOUNDARY — AN OFFER THE SITE MAKES IS NOT CONTENT ABOUT THE EXPERIENCE. If the complaint is
  about something the WEBSITE OR APP ITSELF advertised — a discount on a next purchase, a credit,
  a promo code, a free extra — and the condition attached to it was not stated where the offer
  was made, that is → L1 = "Product Issue" / L2 = "App and Website Issues". Stop here and use
  that, priority rule notwithstanding: this L2 is for what we said about the EXPERIENCE (what it
  includes, where to go, what to expect), and an unstated precondition on our own offer is the
  product failing at the point of sale.
  EXAMPLE: "after you buy tickets the website offers a discount on your next purchase; you only
  get it if you create an account first; Headout will not honour it" → Product Issue.
If the customer paid Headout for entry to an attraction that is actually free at the door (e.g. British
  Museum, some churches, some parks) and we have not mentioned on our website about free entry  → L2 = "Content - Instructions not clear / Misleading Info"




  → L2 =Customer expectation mismatch- 
 "I expected X based on the listing but got Y", "the description said X but it wasn't there",
  "tour was advertised as X but was actually Y", "we only visited one attraction but listing said multiple",
  "unclear description", "didn't know what was included", "no information on the website",
 (Headout sold something misleadingly — this is NOT External Factor / Sold Free Admission)
 
If a venue was closed on the day,  → L2 = "Venue closure"  NOTE: Do NOT use this for meeting point failures. If the customer couldn't find the guide/pickup → Meeting Point Issues.

BOUNDARY: If the customer complains about skip-the-line, priority access, fast track, or priority lines not working — still had to queue, lines were long despite booking priority — this is NOT a content issue.
  → Use Venue Related Issue / Venue Overcrowding (Venue) (the venue failed to honour the expedited entry / manage queues). This is the SAME bucket as long queues and crowding below; a skip-the-line failure is a queue the guest still had to stand in, NOT a broken facility.
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
  BOUNDARY — STOP BEFORE USING "Refund denied". A refund is denied for a
    reason, and if the reason is another failure of ours then THAT failure is
    the L2 and this one is not. Ask what the refund was FOR:
      - charged for something that should have been free, or for something we
        described wrongly → Content - Instructions not clear / Misleading Info
      - tickets late, invalid, wrong, or never delivered → Ticket Issues
      - could not find the guide, the pickup or the entrance → Meeting Point Issues
      - the app or website failed, or advertised an offer with an unstated
        condition → Product Issue / App and Website Issues
    Use Customer Support Issues ONLY when the refusal itself is the whole
    complaint — the guest changed their own plans, or asked for something we
    never owed, and how we handled the request is what they are reviewing.
    EXAMPLE (Zoomarine): "my child should not have been charged, and they
    refused to refund it" → Content - Instructions not clear / Misleading
    Info. The missing free-child pax type caused the charge; the denial is its
    consequence, and classifying the consequence routes the case to the team
    that refused the money instead of the team whose page took it.

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
  BOUNDARY — A TICKET THE GUEST CANNOT REACH IS A TICKET FAILURE, whatever surface it
  failed on. If the app or website broke and the CONSEQUENCE the guest writes about is
  a ticket they could not get, show or use, that is
  → L1 = "Operations Issue" / L2 = "Ticket Issues".
  This L2 is for the app failing AS the complaint — it would not load, it crashed, a
  feature did not work — with no ticket outcome attached.
  EXAMPLES THAT GO TO TICKET ISSUES: "app malfunctioned, causing issues with accessing
  tickets", "ticket not available on app". A labelled sample placed both there.

If Headout's website or app ADVERTISED something without stating the condition attached to it —
an offer, a discount, a credit, a free extra — and the guest only learned of the condition when
they tried to claim it:
  → L2 = "App and Website Issues"
  This is a PRODUCT ISSUE, not an Operations one. The site is Headout's product, and a product
  that states an offer and withholds its precondition has failed at the point of sale, whatever
  the support team did afterwards.
  EXAMPLES: "after buying, the site offered a discount on my next purchase; you only get it if
  you created an account first, and they will not honour it", "the banner promised 10% off and
  the code was rejected at checkout", "it said free cancellation and the condition was on
  another page"
  BOUNDARY: the wording on the EXPERIENCE PAGE about what the tour includes, where to meet, or
  when tickets arrive is content — that stays Operations Issue / Content - Instructions not
  clear / Misleading Info. This clause is for what the SITE ITSELF promises the guest.

[SUPPLY PARTNER ISSUE — Guide quality / Operator's fault]
→ L1 = "Supply Partner Issue"

If a tour guide never showed up at the meeting point and the customer confirms the guide was simply absent
(not that they couldn't find the guide):
  → L2 = "Guide No Show"
  BOUNDARY: customer couldn't locate the guide but guide may have been there → Operations / Meeting Point Issues

If a tour guide provided poor quality guiding — irrelevant information, inexperienced, unclear explanations,
wrong facts, couldn't answer questions, couldn't be heard, or rushed through the tour:
  → L2 = "Guide providing irrelevant/inexperienced/not clear"
  *** THE GUEST GOT A DIFFERENT TOUR FROM THE ONE THEY BOOKED. *** Sub-theme
  "I. Booked Tour Not Provided", NOT "E. Guide Quality Issue". The booking names the
  variant that was sold — "Spanish Guided Tour", "French Guided Tour", "English Guided
  Tour" — so a guest who bought one and was given another did not receive the product
  they paid for. The guide may have guided perfectly; that is not the failure.
  READ WHAT THE REVIEW IS SAYING: not "the guiding was poor" but "this was not the
  tour I booked".
  EXAMPLES: "guide spoke only English despite booking for a different language",
  "guide only spoke English and French, not Spanish as paid", "the requested French
  language was not provided during the tour", "guide only spoke Spanish, leaving
  English speakers uninformed", "booked a private tour and got a group tour".
  Language is the commonest form of this and not the whole of it — variant, group
  size and included stops fail the same way.
  BOUNDARY — A HUMAN GUIDE, NOT AN AUDIO GUIDE. If what was in the wrong language is
  the AUDIO GUIDE ("audio guide not available in German", "could not download the
  Spanish audio guide"), that is → L1 = "Product Issue" / L2 = "Audio Guide Issues" /
  sub_theme "D. AG Language Issues", and this sub-theme does not apply. In a labelled
  sample 5 of 11 language complaints were about the audio guide, so this is the more
  common half, not an edge case.
  EXAMPLES: "guide gave wrong information", "guide was inexperienced", "guide couldn't explain clearly",
  "guide spoke too fast", "guide had bad English", "guide provided incorrect facts",
  "could not hear the guide", "guide couldn't answer our questions", "guide gave unnecessary information",
  "guide was in a hurry", "guide rushed through everything", "guide was unclear"
  NOTE: Only use when the experience involves a human tour guide — not for self-guided/app-based experiences.
  BOUNDARY: For performances, shows, musicals, or entertainment (sub_category: Musicals, Shows, Theatre,
  Performances) — audio/sound quality issues, unclear performers, poor acoustics → Venue Related Issue /
  Venue facility issue. These have no tour guide.

If a tour guide OR ANY ON-SITE STAFF was rude, impolite, racist, unprofessional, or
behaved inappropriately toward customers:
  → L2 = "Guide Behaviour Issues"
  EXAMPLES: "guide was rude", "guide was impolite", "guide was racist",
  "guide was aggressive", "guide made us feel unwelcome", "guide was dismissive",
  "guide was not paying attention", "guide was inattentive"
  *** "STAFF" IS THE SUPPLY PARTNER'S PEOPLE. *** Whoever the guest dealt with on the
  day — venue staff, museum staff, cruise crew, lounge staff, a driver, ticket-desk
  staff — their conduct is the partner delivering the experience, and it belongs here.
  This rule used to send venue staff to Venue Related Issue / Venue facility issue,
  which split one complaint across two L1s on the accident of who employed the person:
  the guest cannot tell, does not care, and the team that fixes it is the same either
  way. A labelled sample put 11 of 13 "rude staff" reviews here.
  EXAMPLES THAT BELONG HERE: "rude staff and poor cleanliness at the airport lounge",
  "staff was unhelpful and ignored me during the cruise", "unfriendly treatment and
  accusations from museum staff", "beautiful castle, but staff were untrained and
  unhelpful", "rude staff made us buy tickets again"
  BOUNDARY — CONDUCT, NOT CONDITIONS. Venue Related Issue / Venue facility issue is for
  the PLACE: dirt, broken equipment, missing signage, no drinking water. A person
  behaving badly is never a facility.
  BOUNDARY — THE PRIMARY COMPLAINT STILL WINS. Rude staff mentioned alongside a ticket
  or food failure that is the real subject goes to that failure, not here. In the same
  sample, "poor food options and rude staff" → Food & Catering, and "ticket lacked QR
  code and staff was unhelpful" → Ticket Issues.

If a tour guide abandoned, left, or disappeared before completing the tour:
  → L2 = "Guide Left / Abandoned Tour"
  EXAMPLES: "guide disappeared in the middle of the tour", "guide left us alone",
  "guide did not complete the tour", "guide walked off midway", "guide left early without finishing"

If tour started significantly late, ended early, or had unexpected timing changes made by the operator:
  → L2 = "Timing Issues"
  EXAMPLES: "guide cancelled last minute", "tour started 45 mins late", "tour ended early",
  "unexpected reschedule", "timing changed without notice", "guide was late"
  DURATION IS TIMING. "Too short", "cut short", "shorter than expected", "duration",
  "visit time reduced" is how much experience the guest got, and that is this L2 — not
  guide quality, even when the guest also says the guide rushed.
  TIEBREAK, and it is a genuine judgement rather than a bright line: ask what the guest
  is short of. Short of TIME → Timing Issues ("the cruise was too short", "the tour felt
  rushed and lacked sufficient time"). Short of SUBSTANCE → Guide providing
  irrelevant/inexperienced/not clear ("rushed and lacked detailed explanations", "too
  short and not worth the price"). In a labelled sample this phrasing split 11 to 7
  between the two, so where both readings hold, prefer Timing Issues and let the
  reviewer move it.

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
def _denotes_english(lang: str) -> bool:
    """True when `lang` names English in any of the forms this system stores it.

    The label is NOT trustworthy. `parse_review` hard-codes "en" on ingest;
    `reply_language.resolve_language` later overwrites it with the full name
    the detector returns — "English"; other intake paths write locale codes
    ("en-US", "en_GB"). All three mean the same thing, and any of them can
    reach `translation_prompt` as the review's stored language.
    """
    l = (lang or "").strip().lower().replace("_", "-")
    return l in ("en", "eng", "english") or l.startswith("en-")


def translation_prompt(body: str, lang: str) -> str:
    # THE ESCAPE IS IN EVERY BRANCH, and that is the whole fix. The explicit-
    # language branch used to be an UNCONDITIONAL "translate into English" with
    # no way to say "this is already English". So a review whose language had
    # been detected and stored as "English" (the common case — English reviews
    # keep an empty body_english, so the inbound translate step re-enters on
    # every later run) was handed to that branch and paraphrased English into
    # English, then stored as an "English translation". The label cannot be
    # trusted to keep English out of this branch, so the branch itself must
    # offer ENGLISH_ALREADY.
    known_non_english = bool(lang) and lang not in ("en", "auto", "") \
        and not _denotes_english(lang)
    if known_non_english:
        return f"""Translate this Trustpilot review into clear English.
Preserve tone exactly — frustration, sarcasm, urgency. Translate, do not paraphrase.
If it is in fact already written in English, reply with exactly the word: ENGLISH_ALREADY
Return ONLY the English translation, or the word ENGLISH_ALREADY. No preamble, no label.

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
# ═════════════════════════════════════════════════════════════════════════
# Worked examples — CX's own labels, rendered into the classification prompt
# ═════════════════════════════════════════════════════════════════════════
_EXAMPLES_PATH = os.path.join(os.path.dirname(__file__), "data",
                              "classification_examples.json")


def classification_examples_block() -> str:
    """Real reviews with the label CX gave them, for the model to pattern-match.

    WHY EXAMPLES AND NOT MORE RULES. Some boundaries in this taxonomy cannot be
    written down. "Guide spoke only English despite booking another language"
    and "Guide only spoke English and French, not Spanish as paid" were labelled
    E and G by the same team; Ticket A-vs-B-vs-D and Audio Guide A-vs-B-vs-E
    split the same way. A rule invented to cover those would be a guess dressed
    as policy. Examples carry the distinction without anyone having to state it,
    which is the only honest way to pass on a judgement nobody can articulate.

    MOST OF THESE ARE CASES THE CLASSIFIER GOT WRONG. An example the model
    already handles teaches it nothing; the ones it missed are where the
    information is.

    A MISSING OR BROKEN FILE SAYS SO IN THE PROMPT. Silently dropping this
    section would leave a prompt that looks complete and classifies worse, with
    nothing anywhere naming the loss — the first rule of CLAUDE.md, in a
    prompt builder.
    """
    try:
        with open(_EXAMPLES_PATH, encoding="utf-8") as f:
            ex = json.load(f)
    except Exception as e:
        return ("\n\nWORKED EXAMPLES: unavailable "
                f"({type(e).__name__}) — classify from the rules above alone.\n")
    if not ex:
        return "\n\nWORKED EXAMPLES: none on file.\n"
    lines = [
        "", "━" * 40,
        f"WORKED EXAMPLES — {len(ex)} reviews with the label CX gave them.",
        "These are the answer where a rule above is ambiguous. Match the shape of",
        "the review, not its subject: the venue and the wording change, the",
        "distinction does not.", "",
    ]
    cur = None
    for e in ex:
        pair = (e["l1"], e["l2"])
        if pair != cur:
            cur = pair
            lines.append(f"  [{e['l1']} / {e['l2']}]")
        st = e.get("sub_theme") or "null"
        lines.append(f'    "{e["review"]}"')
        lines.append(f"        -> sub_theme: {st}")
    lines += ["", "━" * 40]
    return "\n".join(lines)


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
{classification_examples_block()}

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
        # The grounds the dropdown offers. Here as well as in the YAML for
        # the same reason every other key is: someone deleting the block while
        # editing must not leave the control with no options at all.
        "reasons": [
            "Review Takedown Sent",
            "Final Resolution WIP",
            "Severe negative experience (HO Led) - content issues, booking/support issues",
            "Sensitive cases - Uncontrollable - personal emergency, health issues",
            "Untraceable",
            "Other - not listed here",
        ],
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
# The grounds for asking Trustpilot to remove a review, in the order the copy
# file lists them. Plain strings - the text IS the value, because that is what
# the associate picks and what gets recorded.
TAKEDOWN_REASONS = [str(r) for r in (MACROS["takedown"].get("reasons") or []) if r]

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
2a. A FIELD THAT DOES NOT APPLY IS "", AND YOU DO NOT SAY WHY. Most events are
   one-sided: an agent's reply has no guestSaid, a guest's message has no
   weDid. Return an empty string for those and nothing else. Do NOT write
   "N/A", "Not applicable", "this is an agent response event", or any other
   explanation — every one of these fields is rendered on the card and in the
   Slack post AS WHAT THAT PERSON SAID, so an explanation addressed to us
   appears in quotation marks as the guest's or the agent's own words. Rows
   have shipped reading `guest: N/A — this is the guest's reply event`.
   An empty field renders as nothing, which is correct and is what we want.
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
- guest_name — the REVIEWER NAME above, unless the text clearly names a
  different person as the booker, in which case use that. It is often the only
  indicator available, so never omit it. If and only if the reviewer name is
  "(not provided)" and the text names nobody, return null — never the word
  "unknown".

  This name is searched against the Zendesk requester, so it must be the NAME
  and nothing else. Two rules, both of which have cost a match:

  1. DROP SALUTATIONS AND SUFFIXES. "Mr", "Mrs", "Ms", "Miss", "Dr", "Prof",
     "Sir", "Jr", "Sr", "II", "III" are not part of anyone's name and match no
     Zendesk requester.
       "Mrs Fredrik Olsen"      -> "Fredrik Olsen"
       "Dr. Salim Bhayani Jr"   -> "Salim Bhayani"

  2. KEEP EVERY NAME TOKEN, INCLUDING THE MIDDLE ONE. Do not shorten a name to
     first-and-last. The middle name is frequently the most distinctive part
     of it, and dropping it is what turns a single confident match into ten
     weak ones.
       "Bhayani Salim F"        -> "Bhayani Salim"     NOT "Bhayani F"
       "Fredrik Martin Olsen"   -> "Fredrik Martin Olsen"

     A bare INITIAL is not a name token. Drop a standalone single letter —
     "F", "F." — because searching on one letter matches a great many people
     and ranks none of them. Keep everything of two letters or more: "Li",
     "Bo" and "Ng" are real names.

  Copy the tokens you keep verbatim, in the order they appear. Do not reorder
  them, do not correct the spelling, and do not expand an initial into a guess.
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
   Trustpilot display name. EVERY token you return is searched, so a salutation
   left in narrows the search to nothing and a middle name left out widens it
   to everyone sharing a surname.
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
def _support_tickets_block(frames) -> str:
    """Each guest contact as what they ASKED and what we REPLIED.

    The prompt was given `support_summary` — one worked-out line about the arc
    of the case — and nothing else about the tickets. That is enough to say a
    conversation happened and not enough to answer either of the questions the
    case findings have to answer: why did the guest reach out, and did we solve
    it. A guest does not contact us without a reason, and the reason is in the
    ticket.

    Only CONVERSATIONS. The same predicate the card and the Slack post use, so
    a booking dump cannot arrive here as something the guest said.

    Says how many were dropped and why, rather than showing a short list that
    reads like the whole of it.
    """
    rows = [f for f in (frames or []) if isinstance(f, dict)]
    if not rows:
        return "(no support contact was found on this booking)"
    try:
        from server.services.zendesk import is_conversation
        convos = [f for f in rows if is_conversation(f)]
    except Exception:
        convos = rows
    moved = len(rows) - len(convos)
    if not convos:
        return (f"(no conversation with the guest on this booking — "
                f"{moved} system event(s) only, so nobody spoke to them)")
    out = []
    for f in convos[:20]:
        from server.services.zendesk import guest_words as _gw
        said = (_gw(f) or str(f.get("summary") or "")).strip()
        did  = str(f.get("weDid") or "").strip()
        gap  = str(f.get("gap") or "").strip()
        zd   = str(f.get("ticket_id") or "").strip()
        bits = [f"- {f.get('time') or '?'}"]
        if zd:
            bits.append(f"(ZD-{zd})")
        bits.append(f"guest: {said or '—'}")
        bits.append(f"| we: {did or '— (no reply recorded)'}")
        if gap:
            bits.append(f"| gap: {gap}")
        out.append(" ".join(bits))
    if moved:
        out.append(f"({moved} system event(s) on this booking are NOT listed "
                   f"here — they are machinery, not contact)")
    if len(convos) > 20:
        out.append(f"({len(convos) - 20} further contact(s) not shown)")
    return "\n".join(out)


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
    support_frames: list = None,
) -> str:
    """
    Generates the RCA v3 shape: what_went_wrong
    (the 5 mandated headings), booking_logs, flags (checklist run silently,
    failures only), support_interaction / sp_interaction (each carrying
    zd_ref), area_of_improving, takedown.

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

    # THE MACRO IS THE REPLY, not a register to imitate. It was a "tone
    # example" and the model wrote its own reply beside it — which is how a
    # reply nobody approved reached the card in the approved one's voice. The
    # macro has been chosen for this case by a selector that read the review,
    # and gated so what it promises is a remedy the DSS actually named; a model
    # paraphrasing it can only lose one of those properties.
    #
    # ONE macro, not three. Three "examples" is a pattern to blend; one is the
    # text to work from. The selector picks it.
    if canned_list:
        _m = canned_list[0] or {}
        _promises = _m.get("promises") or []
        tone = (
            "THIS IS THE APPROVED REPLY FOR THIS CASE. Use its wording as the\n"
            "backbone of `suggested_response`.\n\n"
            f"[approved macro — {(_m.get('situation') or '').strip()}]\n"
            f"{(_m.get('response') or '').strip()}\n\n"
            "HOW TO USE IT:\n"
            "- Keep the macro's approved sentences. Do not rewrite it into your\n"
            "  own words, do not shorten it to a summary, do not restructure it.\n"
            "- ADDRESS WHAT THIS GUEST ACTUALLY RAISED. The macro covers the\n"
            "  general situation; this guest also named specifics. Work those in\n"
            "  — a reply that ignores half their complaint reads as a form\n"
            "  letter, which is what they are already angry about.\n"
            "- Fill every placeholder from the record: <first name>/<Name> from\n"
            "  the guest's name, <date>, {{experience}}, <$X>/<X%> from the\n"
            "  booking. Never send a placeholder through, and never invent a\n"
            "  figure to fill one — if the record does not have it, rephrase so\n"
            "  the sentence does not need it.\n"
            + (f"- This macro commits us to: {', '.join(_promises)}. That is\n"
               "  authorised for this case. Do not offer anything beyond it.\n"
               if _promises else
               "- This macro promises the guest no compensation. Do not add any:\n"
               "  no refund, no credit, no coupon. Nothing has authorised one.\n")
            + "- The voice rules above are hard policy and outrank the macro's\n"
              "  own phrasing where they conflict.")
    else:
        # No approved macro fits this review. The instruction that used to sit
        # here - "write in plain, warm, direct English" - got a reply that read
        # exactly like an approved one and was not: same register, same shape,
        # no review behind it. An associate cannot tell those apart on the
        # card, so the model is told to return null and rule 20 backs it up.
        tone = ("(NO APPROVED MACRO MATCHES THIS REVIEW. Return null for "
                "suggested_response — see output rule 20. Do NOT write one.)")

    out = RCA_V3_TEMPLATE
    for token, value in {
        "<<CANNED_TONE>>":      tone,
        "<<REVIEW_ID>>":        review_id,
        "<<L1>>":               l1 or "",
        "<<L2>>":               l2 or "",
        "<<SUB_THEME>>":        sub_theme or "",
        "<<SCENARIOS_ROUTED>>": ", ".join(routed) or "(none routed)",
        "<<REVIEW_TEXT>>":      review_text or "",
        "<<BOOKING>>":          json.dumps(_readable_booking(bk), default=str),
        "<<TIMELINE>>":         json.dumps((timeline or [])[:30], indent=2, default=str),
        "<<ZENDESK_RAW>>":      "\n".join(raw_lines) or "(no raw ticket bodies)",
        "<<TICKET_FACTS>>":     (json.dumps(_tf, indent=2, default=str)
                                 if _tf else "(no structured facts extracted)"),
        "<<INSIGHTS>>":         json.dumps(insights or {}, default=str),
        "<<DSS>>":              json.dumps(dss_rec or {}, default=str),
        "<<SUPPORT_SUMMARY>>":  support_summary or "(none)",
        "<<SUPPORT_TICKETS>>":  _support_tickets_block(support_frames),
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
  The SP escalation email address is deliberately NOT provided to you. Do not
  comment on whether the SP has an escalation email, whether one is on record,
  or whether a formal escalation email could or could not be sent — you have no
  data on that. Judge escalation only from the partnered / opt-out flags and
  from what the tickets actually show was done.

WHERE FACTS LIVE — the only sources you may verify against, routed by claim.
Each maps to a `source` value used in `evidence[]`:

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

  There is NO evidence source for the DSS sheet. Look the needle up — that is
  how you know whether a control existed and whether it was followed — but the
  lookup is not evidence. An evidence row records what happened to THIS
  booking; "no DSS row covers this scenario" is a remark about our own
  paperwork, and it was reaching the reader's evidence list beside records of
  what actually happened. What the sheet prescribes goes in `dss.prescribes`,
  which is its own field and is where a reader looks for it.

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

ZENDESK TIMELINE (structured) — READ THIS CHRONOLOGICALLY BEFORE YOU DECIDE
WHAT THE ISSUES ARE. It is what the guest asked us for and what we did about
it, in order. The review is the ending of that story; this is the story.
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

THE SUPPORT TICKETS THEMSELVES — what the guest actually wrote and what we
actually replied, per contact. This is the source for "why did they reach out"
and "did we solve it". A one-line arc summary was all this prompt used to get,
which is not enough to answer either: it says there was a conversation without
saying what was asked or whether it was resolved.
<<SUPPORT_TICKETS>>

SUPPORT SUMMARY — the arc of the support case, already worked out for you.
Use it to write `case_side` on each issue. It was pasted in here and referred
to by no rule, so it was ignored on every card.
<<SUPPORT_SUMMARY>>

APPROVED REPLY FOR THIS CASE — the backbone of `suggested_response`.
Chosen for this review and gated so what it promises is a remedy the DSS
named. Keep its sentences; work in what this guest actually raised.
<<CANNED_TONE>>

━━ ISSUE-SPECIFIC QUESTIONS — CHECKS TO WRITE AGAINST, not a section to fill in ━━
Answer every one of these against the backend before you write anything. They do not
appear in your output and there is no field for them: what they produce is a verdict, a
root cause and an SOP gap that are consistent with what the record actually shows. Where
one surfaces something we missed, rule 12b says where it goes.
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

2. ONE BLOCK HOLDS ONE VERDICT AND ONE FIX. The test is NOT "how many things
   did the guest mention". It is:

       Does this need a SECOND VERDICT, or a SECOND FIX WITH A DIFFERENT OWNER?

   Either one means a second block. Neither means ONE block, however many
   sentences the guest spent on it.

   TWO BLOCKS — different fixes:
     "tickets took two hours"           fix: Selenium failure alert   owner RO
     "I never heard this before I paid" fix: window on the page       owner Content
   Two gaps, two owners. The guest wrote one grumble; it lands on two desks.

   TWO BLOCKS — different verdicts:
     "I booked the summit"       Inaccurate — the booking shows 2nd floor
     "the listing is confusing"  Partly accurate — the variant names are close
   One block cannot carry both, because `claim_accuracy` is a single value.

   ONE BLOCK — same fix:
     "tickets arrived late and I was left standing outside the venue"
   The waiting is the CONSEQUENCE of the lateness, not a separate grievance.
   Consequences belong in `claim`, not in a block of their own.

   ONE BLOCK — one gap described twice:
     "the guide never showed up, we waited 40 minutes for nobody"
   Same fact, said twice for emphasis.

   YOU SPLIT ON VERDICTS AND FIXES, NEVER ON CAUSES. A failed run, no
   monitoring and no DSS path is ONE gap at three depths, not three blocks.
   When unsure, write the fix for each candidate block. If you find yourself
   writing the same action twice, it was one block.

2a. FILL IT IN THIS ORDER: `claim` (the guest's words, verbatim, no quote
   marks — null if you inferred the complaint rather than them stating it),
   then `evidence` (read the records BEFORE any verdict), then
   `claim_accuracy` (the verdict follows the evidence, never what seems
   plausible), then everything else.

   AND `claim_accuracy` JUDGES THE REVIEW'S CLAIM — NOTHING MORE.
     It does NOT decide whether there is a diagnosis. A verdict on what the
     guest wrote publicly and a finding about what happened to the booking are
     two different things, and this rule used to conflate them: Inaccurate or
     Unknown nulled root_cause, operational_failure, sop_gap and fix outright.
     So a review saying "the policy is too strict and support was unhelpful",
     against a booking where the guest HAD asked to move their date and been
     refused, scored Inaccurate and the whole chain was deleted. The card then
     said the guest was wrong and showed nothing else — the modification
     request they actually made never appeared anywhere.
     "The public claim is inaccurate AND the booking had a real problem" is a
     normal case and it must be expressible.
     Accurate, Partly accurate  ->  root_cause and fix are REQUIRED.
     Inaccurate, Unknown        ->  they are required IF `case_side` is not
                                    null — i.e. the Zendesk case shows
                                    something happened. With nothing in the
                                    case and a claim that does not hold, they
                                    are null: there genuinely is nothing to
                                    diagnose.
   operational_failure and sop_gap stay OPTIONAL throughout: they exist when
   we got something wrong, and inventing one to fill a field is worse than
   leaving it null. Do not fill them to look thorough. A root cause under an Inaccurate
   verdict is the shape of diligence with nothing behind it, and somebody
   acts on it.

2c. THE THREE CAUSAL FIELDS ARE THREE DEPTHS.
     `root_cause`          WHAT BROKE. The mechanism.
     `operational_failure` WHY IT BROKE. Why that mechanism could fail.
     `sop_gap`             WHY NOTHING CAUGHT IT. The control that should have.

   Each is a level beneath the last. IF TWO OF THEM COULD BE THE SAME
   SENTENCE, TWO OF THEM ARE WRONG.

   YES  root_cause           The Selenium run returned no ticket URLs.
        operational_failure  Nothing watches Selenium runs, so nobody knew.
        sop_gap              No DSS path covers a same-day booking with a
                             failed run.

   NO   root_cause           Selenium fulfilment failed.
        operational_failure  The Selenium run did not complete.
        sop_gap              Selenium failure was not caught.
   Three sentences, one fact. Ask of each: WHAT DOES THIS ADD THAT THE ONE
   ABOVE IT DID NOT SAY? If nothing, that field is null.

   `operational_failure` IS NULL MORE OFTEN THAN NOT. It is not "the
   mechanism, restated" — it is the reason that mechanism was allowed to
   exist. If you do not know why the gap was never closed, you do not have
   one.
     NO   root_cause           The booking flow has no Baby/Infant pax type.
          operational_failure  The pax-selection UI presented a single Child
                               category without differentiating the tiers.

2d. `fix` IS THE NEGATIVE OF A GAP YOU FOUND. `because` restates the gap;
   `action` closes it. Read them together — the action must be the negative of
   the gap, or the fix is invented.

     YES  because  The experience page states no delivery window
          action   Add the two-hour delivery window to the experience page
     NO   because  We replied to the guest in four minutes
          action   Improve response times

   IF NO EVIDENCE ENTRY SHOWS A GAP, there is nothing for `because` to point
   at and `fix` is null. The fix addresses WHAT THE EVIDENCE SHOWS, not what
   would have made the guest happy — not "notify guests earlier", not "review
   the SLA". Those are opinions about a better world, not corrections to a
   documented gap.

   A structural fix needs a pattern behind it. `pattern` carries the count; do
   not repeat it anywhere else. Where `pattern` is null, the fix is scoped to
   this case.

   `because` is THE GAP, ONE CLAUSE. Not the rule, and not the rule plus the
   gap.
     NO   because  The page states children under 1.00 m enter free but a
                   booking is required, and no Baby option exists
     YES  because  No Baby/Infant option exists for the guest to select

2f. USE DSS TO DETERMINE WHAT THE NEXT STEP SHOULD HAVE BEEN, THEN NAME THAT
   STEP. DSS is a LOOKUP, never a subject. Look up the needle for this
   scenario BEFORE you answer, work out what the correct next escalation step
   would have been, and write THAT — not the sheet, not its coverage, not
   whether a row exists.
     A path exists and was followed  ->  null. Say nothing.
     A path exists and was skipped   ->  name the STEP, not the outcome.
     No path covers this scenario    ->  REASON THE NEXT ESCALATION STEP from
                                         the playbook you do have, and name
                                         that step. The absence of a row is an
                                         internal fact about our tooling; it is
                                         NEVER the finding.
     DSS unavailable                 ->  null, and name the source in
                                         `claim_accuracy_note`.
   NEVER write a step you cannot find in DSS. An invented process produces a
   deviation from nothing, and the team is sent to fix a rule that does not
   exist.
   WHERE THE SKIPPED STEP GOES: `dss.missed_next_step`, as
   [{"team", "action"}]. That list is the ONLY thing DSS contributes to
   Actions Taken, and Actions Taken is read as "this is what we did / what
   must be done" — so it carries the step that SHOULD have been taken and was
   not, on the team that would have taken it. An empty list is the correct
   answer whenever the path was followed, and it is not the same as omitting
   the field.

   WRITE THE PROCESS FAILURE, NOT THE SHEET'S COVERAGE. The reader of this
   field owns an operation, not a spreadsheet. "No DSS path governs a
   system-initiated vendor reassignment" tells them about our documentation;
   what they need is what nobody was required to do.
     WRONG: "No DSS path governs a system-initiated vendor reassignment that
            compresses the guest's rescheduling window."
     RIGHT: "Nobody was required to contact the guest when the reassignment
            compressed the rescheduling window, so it closed unnoticed."
   Same for `fix`. The fix is the correction to the operation, never the
   authoring of a DSS row.
     WRONG: "Define a DSS path for system-initiated vendor reassignments."
     RIGHT: "Require proactive notification to the guest whenever a
            reassignment shortens the rescheduling window."
   NEVER NAME DSS ANYWHERE A READER SEES A FINDING: not in `root_cause`, not
   in `operational_failure`, not in `sop_gap`, not in `fix.action` or
   `fix.because`, and not in any `evidence[].text`. No "no DSS row covers
   this", no "define a DSS path", no commentary on the sheet at all. What the
   matched row prescribes belongs in `dss.prescribes`, which is its own field
   and is where a reader looks for it.

2g. DO NOT REPEAT. The commonest failure is one fact restated across the
   block. Each field earns its place by saying something the others do not.
     * NEVER RESTATE THE REVIEW. `claim` holds the guest's words; every other
       field says something the review does not contain.
     * NEVER RESTATE `root_cause` in `operational_failure` or `sop_gap`.
     * SAY AN ABSENCE ONCE, in `claim_accuracy_note`. Other fields are null,
       not a repeated explanation of what the absence prevents.
     * `claim_accuracy_note` IS THE INFERENCE, `evidence` IS THE FACTS. The
       note says how the facts reach the verdict; it does not list them again.
     * `fix.because` restates THE GAP, not the whole diagnosis. One line, drawn
       from one evidence entry.
     * ONCE A FACT IS IN `evidence`, REFER TO IT — DO NOT RESTATE IT. The
       analysis fields exist for what the evidence does not say. If a figure
       or a rule appears in evidence, later fields say "the free tier" rather
       than repeating "under 1.00 m, free, booking required".
     * 25 WORDS IS A HARD CEILING, NOT A TARGET. A 40-word field is two
       sentences welded together — split it, or cut the half that restates
       something above.
     * A ONE-LINE REVIEW PRODUCES A ONE-LINE RCA. Length comes from the case
       having more in it, never from saying the same thing more ways.

2e. `backs_claim` — DOES THIS ENTRY SHOW THE GUEST WAS RIGHT?
     Yes   the guest is right about this
     No    the guest is wrong about this
     null  NOT ABOUT THE CLAIM — it establishes mechanism or sizes a pattern

   CHECK THE TIMING BEFORE ANSWERING No. This is where it goes wrong:
     claim        "I never heard this before I paid for them"
     evidence     "The booking confirmation email states tickets arrive within
                   two hours" — sent 15:22, payment 15:21
     backs_claim  null, NOT "No"
   The email went out AFTER payment, so it disproves nothing about what they
   knew at checkout. A wrong No is worse than none, because it reads as
   settled.

2b. EVIDENCE IS SHORT AND ON THE CLAIM. Each `evidence[]` entry is ONE short
   sentence — target 15 words, 25 is the ceiling — and it must bear on the
   `claim` of the issue it sits under. Nothing else belongs there.
     The test: read the claim, then read the entry. If the entry does not make
     that claim more or less likely, DELETE it. It is context, and context
     belongs in root_cause or pattern.
     NO   "SP confirmed the 12:30 PM booking on 30 Jul with partner reference
           RSZV JK8, and the system showed it as confirmed at the time CE
           denied the refund."           (two facts, 28 words, one sentence)
     YES  "SP confirmed the 12:30 slot on 30 Jul; ref RSZV JK8."
     YES  "Our system still showed it confirmed when CE refused the refund."
           (the second fact, as its own entry — one fact per entry)
     NO   "Italy Pass is a partnered vendor, but the escalation email field is
           blank, meaning a formal SP escalation email could not be sent."
           (true, and it says nothing about whether the refund was owed)
   A PATTERN COUNT IS NOT EVIDENCE. It does not verify the claim — its
   `backs_claim` would be null — so it belongs in `pattern`, not here. One
   count, one place.

   ONE FACT, ONE ENTRY. Two sources stating the same rule is ONE entry unless
   the second contradicts or qualifies the first.
     NO   exp-page  Children under 1.00 m enter free but a booking is required
          zendesk   Ticket notes state infants under 1.00 m enter free but a
                    reservation is required

   Three to five entries per issue is normal. More than six means context is
   being filed as evidence.

   `time` IS REQUIRED WHERE THE RECORD SHOWS WHEN IT HAPPENED, in the same
   "02 Aug 09:13" form the timeline uses — see the `time` rule under
   `case_findings`, which governs these entries as well. Evidence is rendered
   in that ordered list, so an entry without a time cannot be placed in it.

3. DIAGNOSE, DON'T DESCRIBE. Name the concrete failing step. Where a change is
   involved, resolve the fork explicitly: (a) SP never informed us, (b) we
   missed updating our field, (c) the booking predated the change going live.
   NEVER accepted as a root cause: a restatement of the review, "awaiting SP",
   or "raised with Tech" without the technical-vs-operational call.

4. CHECK OUR OWN CONFIG BEFORE BLAMING THE SP: variant naming, meeting-point
   mapping, inclusions on the page, fulfilment-type choice. Often we are the
   root cause. Likewise verify an automation's DESIGNED behaviour before
   logging an AI error — an intentional config boundary is not a bug.

4g. A CLAIM ABOUT THE AMOUNT IS NEVER SETTLED BY THE BOOKING RECORD ALONE.
   "Booking 32142070 records one adult, CHF 461.19 total, no add-ons" was
   offered as proof that a guest claiming a double charge was wrong. It proves
   nothing: it is the record confirming its own pax count. The question is not
   how many tickets we recorded — it is whether CHF 461.19 is the price of ONE
   of them or TWO.
   Settling it needs a UNIT price, which the total and the pax count cannot
   supply between them. ONE source has it: the ZENDESK ticket text, where
   booking dumps and confirmation emails routinely state per-person amounts.
   "net" is what we paid the PARTNER, never what the guest paid; reading one
   for the other tells a guest they were charged 450 when they paid 606.
   THE EXPERIENCE PAGE IS NOT A SOURCE and must not be cited as one. It is
   keyed on TGID while the price depends on the TID actually booked, so "the
   price on the page" is the wrong number for any non-default variant — which
   is the booking someone disputes.
   With no unit price in the case, the verdict is "Unknown" and the note says
   the amount could not be verified from the Zendesk case and should be
   checked manually. NOT "Inaccurate": a guest wrongly told they were not
   double-charged is worse than one told we could not tell.
   THIS IS ALSO ENFORCED IN CODE, in `price_check.gate_amount_claim`, which
   demotes a definite verdict on an unsettled amount claim on the way out and
   says so on the trail. The rule is here so the draft is right the first
   time; the gate is there because a stored draft cannot be re-asked.

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

6. AN OPERATIONAL FAILURE IS SOMETHING THE RECORD SHOWS. Do not infer one
   from the guest being unhappy, from a question you could not answer, or from
   an outcome you would have handled differently. All three below must hold,
   and you must be able to point at each.

   (a) THE RECORD SHOWS IT — a Zendesk ticket, the booking record, the BMS
       record, the fulfilment log or the experience page.

       AN EXPECTED THING MISSING IS ALSO A RECORD, and it is the most common
       real failure: a ticket open four days with no agent reply, an
       escalation never raised, a refund never issued. Cite what should be
       there and is not — the ticket that stayed open, the field that stayed
       empty.

       BUT MISSING BECAUSE WE DID NOT LOOK IS NOT MISSING. If the source was
       unavailable — Zendesk not searched, a failed query, a booking that
       never resolved — you are looking at a gap in the DATA, not a gap in the
       handling. Those two are indistinguishable unless you check which
       happened, and reading the first as the second invents a failure out of
       an outage. Say which in `claim_accuracy_note` and write null.

   (b) IT IS THIS BOOKING'S. Name the record: the ZD id, the booking id, the
       date. A failure on another ticket, another booking or another guest is
       not this one's, however similar. Where a contact is involved, name
       WHICH — "the tickets" is not a citation when there are three.

       NOT EVERY FAILURE HAS A CONTACT. A wrong meeting point, a mis-set
       field, a listing error can all fail a guest who never wrote in. Those
       are operational failures with no ticket behind them, and (b) is met by
       the booking or the page instead.

   (c) IT EXPLAINS WHAT THE GUEST EXPERIENCED ON THIS ISSUE. The test is
       CAUSE, not vocabulary — the guest need not have named it. "The tickets
       never arrived", with the record showing we captured their email address
       wrong, is a match: they described the effect and you found the cause.
       What does NOT match is a failure that produced a different effect — a
       slow refund is not the operational failure for a meeting-point
       complaint. That belongs to its own issue, or to nothing.

       IT MUST ALSO COME FIRST. Something we did after the review was posted
       did not cause the review. Check the timestamps before attributing it.

   WHOSE FAILURE. `operational_failure` is OURS — Headout's people or systems.
   A vendor cancelling, a venue closing, a strike: those are facts about the
   world and they belong in `root_cause`. They become an operational failure
   only where WE mishandled them — see rule 7.

   If any of the three is missing, `operational_failure` is null and the
   reason goes in `claim_accuracy_note`. Null is a finding. An invented
   operational failure is worse than none, because it reads as verified and
   sends somebody to correct a person who did nothing wrong.

   A CORRECT ACTION IS NOT A FAILURE, however unhappy it left the guest. What
   counts as correct is judged against policy and the DSS needle, and that
   judgement is set out once in rule 6b — apply it here too rather than
   deciding afresh.

6b. `sop_gap` — WAS THERE A PROCESS, AND WAS IT FOLLOWED? Look it up before
   you answer. This is a different question from rule 6: `operational_failure`
   is what a person or system DID wrong; `sop_gap` is the PROCESS that was
   skipped or was never there. The same issue can have one, both or neither.

   Worked example. The guest chats asking to cancel because of a health
   problem. Read the DSS needle for that scenario. Suppose it prescribes:
   request documentation, then refund or HOC on medical grounds.

     CE asked for documentation and applied it   -> sop_gap is null. The
       process existed and was followed. Say nothing.
     CE denied it flat, no documentation asked   -> operational_failure is
       "CE denied a medical-grounds request without requesting documentation"
       (what was done); sop_gap is "the DSS medical-grounds path was not
       applied" (the step that was skipped). Name the step, not the outcome.
     DSS has no row for this scenario            -> sop_gap is null, unless
       the ABSENCE is itself the finding — say plainly "no DSS path covers a
       medical-grounds cancellation", which is a gap in the SOP rather than a
       gap in the handling. Never write a step you cannot find in DSS: an
       invented process produces a deviation from nothing, and the team is
       sent to fix a rule that does not exist.
     DSS prescribes something that did not fit   -> sop_gap names the
       deficiency in the process, not the agent. "The path assumes the guest
       can reschedule; this experience is single-date."

   THE SAME EVIDENCE RULES APPLY as rule 6. A step recorded nowhere as done is
   a step not done — but only if the record was actually read. If Zendesk was
   not searched or the DSS needle is unavailable, that is a gap in the DATA:
   sop_gap is null and `claim_accuracy_note` says which.

   HOW TO JUDGE THE HANDLING. This governs `sop_gap`, `ce_miss` and any flag
   about how a case was handled — one standard, applied in all three.

   1. AGAINST POLICY, NOT GENEROSITY. That we could have been kinder is not a
      miss. Judge against the DSS needle and standing policy, never against
      what a sympathetic reader would have preferred.

   2. STANDING POLICY. An out-of-policy cancellation or modification request
      is DENIED first — a correct denial is never a CE miss. If the guest
      persists, HOC scaled to the issue is the sanctioned path, and HOC after
      persistence is not a deviation either.

   3. WHAT A REAL DEVIATION IS. Exactly one of: an in-policy request denied; a
      DSS-prescribed action skipped; or comp granted with no policy basis and
      no recorded persistence. Anything outside those three is not a deviation
      — it is a different outcome from the one you would have chosen.

   4. THE NEEDLE'S OWN EDGE CASES.
      Empty DSS, or match_score 0 -> the needle did not match. Judge against
        standing policy and the scenario checklist ONLY. NEVER invent a policy
        to judge against: an invented standard produces a deviation from
        nothing, and this is precisely where a plausible-sounding rule is
        easiest to write and impossible to check.
      DSS forks on "social media" -> every case here IS a public review, so
        the social-media variant always applies. Do not treat it as unresolved.

   5. "WRONG POLICY APPLIED" NEEDS THE RULE IT BROKE. Do not write that an agent
      or an automated bot "applied the wrong policy", "misapplied policy",
      "applied the standard policy and ignored the exception", or "should have
      applied" a different one, UNLESS you can name the specific DSS needle line
      or SOP / scenario-checklist rule it contradicts — and you cite that line as
      the source_ref. You are NOT told our internal automation rules, so a bare
      "the AI bot gave a flat refusal / applied the no-reschedule policy" is a
      guess about a system you cannot see: drop it. A correct-looking denial is
      not a miss merely because an exception request existed elsewhere — name the
      rule that made the denial wrong, or do not raise it. This binds `ce_miss`,
      `sop_gap` and any flag equally.

7. SUPPORT-FAILURE SUPERSEDES. If an external event occurred but CE or RO
   mishandled the contact, the root cause is the mishandling. What did the
   agent DO after acknowledging — escalated, or dropped?

8. SCOPE EVERY FINDING. One-off or pattern? Use the INSIGHTS counts (similar
   reviews, similar support contacts, completion rate) and state the window
   they cover in the issue's `pattern` field. A structural fix without sizing
   gets rejected. If the fault is ours, anything less than a full refund must
   be justified in one line.

9. POINT FORM, SHORT SENTENCES, FINDINGS ONLY. Every string is one short
   complete sentence — subject, verb, full stop. Target 8–14 words; 20 is the
   hard ceiling. "Selenium FF, no disclosure" is too clipped; "The page did not
   state the two-hour delivery window." is right.

9b. PLAIN ENGLISH. WRITE IT THE WAY YOU WOULD SAY IT.
   This is the rule most often broken while every other rule is obeyed: a
   sentence can sit inside the word limit and still be unreadable. These came
   off a real card.

     NO   "SP cancelled the guest's booking and the refund was denied despite
           the cancellation being vendor-initiated, not guest-initiated."
     YES  "The vendor cancelled the booking, then we refused the refund."

     NO   "Confirm vendor-initiated cancellation from booking record and
           process full refund per standing policy; flag vendor cancellation
           rate for RO review."
     YES  "Check the booking record, refund in full, and tell RO this vendor
           keeps cancelling."

   Say who did what. Name the actor — "the vendor", "we", "the agent" — and
   use an ordinary verb. "was denied", "is initiated", "were not provided"
   hide the person responsible, which is the one thing an RCA exists to show.

   Banned outright, because each one adds length and removes meaning:
     - "-initiated" / "-driven" / "-related" compounds. The vendor cancelled it.
     - "process a refund", "action this", "raise with", "flag for review",
       "per standing policy", "as per", "in a timely manner", "at this
       juncture", "going forward", "leverage", "ensure", "facilitate",
       "robust", "seamless", "utilise".
     - Any noun built from a verb where the verb would do: "cancellation" →
       "cancelled", "provision of tickets" → "sending tickets", "the denial of
       the refund" → "we refused the refund".

   Internal shorthand is fine where the reader uses it daily — SP, RO, CE,
   DSS, BID, TGID — and nowhere else. Spell out anything rarer the first time.
   `suggested_response` is read by a GUEST, so none of it appears there at all.

   If a sentence needs reading twice, it is wrong however short it is.
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
    or null when the label says everything.

    ONLY WHEN A BOOKING IS CONFIRMED. This section is the BOOKING's timeline,
    and until an associate has picked one there is no booking whose timeline
    this could be. Narrating the guest's account under that heading put a
    six-event sequence on a card whose booking was still an open question, and
    a reader scanning the column had no way to tell it from a real one.
    <<BOOKING>> is empty or carries no id: RETURN AN EMPTY LIST and say
    nothing. The card explains the emptiness itself.

    THE GUEST'S ACCOUNT IS NEVER AN EVENT. Not when the systems gave you
    nothing, not when the booking is confirmed, not marked "unverified", not
    ever. This section is what the RECORD shows happened — Zendesk tickets,
    fulfilment runs, automated mails, the booking and the review. What the
    guest says happened is a CLAIM, and claims already have a home: they are
    the `claim` and `stated_issue` fields, quoted as theirs and labelled with
    a `claim_accuracy` the record decides.

    An earlier version of this rule said the opposite — build the sequence
    from the guest's own account when the systems gave you nothing, and end
    each `detail` with "(guest's account, unverified)". It produced exactly
    what it promised, and the result was a timeline of four claims and two
    real bookends, sitting under a heading that says these are events. A
    parenthetical does not undo a heading. The reader scans a column of times
    and labels, and "Guide changed meeting point at 08:30" reads as something
    we know, because everything else in that column is.

    Worse, it fired precisely when the record was EMPTY — so the card looked
    fullest exactly when we knew least, and a broken Zendesk lookup was
    indistinguishable from a case where nothing happened. That inversion is
    the reason the rule is gone rather than softened.

    So when the booking is confirmed and the systems gave you nothing: return
    ONLY the entries the record supports — the booking being made and the
    review being posted, which the warehouse always knows. Two entries, or
    one, or none. An empty middle is the honest shape of a case whose systems
    hold nothing, and the card says so itself.

10b. THE BOOKENDS THE WAREHOUSE ALWAYS KNOWS. Two entries are always
    available and give the sequence its ends: the booking being made
    (<<BOOKING_DATE>>) and the visit (<<VISIT_DATE>>). Include both as the
    first and last dated entries whenever they are known, whatever else you
    have. `time` is null ONLY when you have a REAL event whose time genuinely
    is not recorded anywhere.

    There is no "undated" any more. That string existed to carry entries built
    from the guest's account, and rule 10 no longer permits any — so a `time`
    of "undated" now means an entry got in that should not have. If you find
    yourself reaching for it, the entry is a claim and belongs in
    `stated_issue` or a `claim`, not here.

20. NO APPROVED MACRO, NO REPLY. When the tone block above says no approved
    macro matches, `suggested_response` is null. Not a paraphrase of one, not
    "plain warm English", not a reply built from the evidence — null.
    An invented reply is indistinguishable on the card from an approved one:
    same register, same length, same shape. The associate reviewing it has no
    way to tell that nothing behind it was ever signed off, and Send puts it
    on a public review. A blank section that says "no approved macro matches
    this — write the reply yourself" is a smaller cost than a plausible reply
    nobody approved. Every other field is still filled in normally; this rule
    governs `suggested_response` alone.

11. TAKEDOWN IS A FACTUAL TEST, NOT A SENTIMENT ONE. "Yes" only when the review
    is factually false or breaches platform policy. A review that is accurate,
    even partly, is "No" — however harsh its tone. "Untraceable" when no
    booking or contact record exists to check the claims against.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

## OUTPUT FORMAT — return ONLY this JSON object, no prose before or after

{
  "stated_issue": "<2-3 sentences, 60 words MAX: the guest's problem in our words, for the top of the RCA>",
  "l1": "<the L1 category from the taxonomy>",
  "l2": "<the L2 category from the taxonomy, valid for that L1>",
  "sub_themes": ["<sub-theme from the L1::L2 framework, e.g. 'C. Ticket Delayed'>"],
  "scenarios": ["<scenario from SCENARIOS_ROUTED>"],
  "overlay_scenarios": ["<secondary scenario, not already in scenarios | omit if none>"],
  "what_went_wrong": {
    "case_findings": [
      {"text": "<one line — a thing the RECORDS show happened on this booking>",
       "source": "<booking | bms | zendesk | insights | exp-page>",
       "time": "<DD Mon HH:MM | DD Mon | null>"}
    ],
    "fixes": [
      {"action": "<what gets done>",
       "owner": "<one of the nine team codes — the team that must DO it>",
       "because": "<the gap it closes>"}
    ],
    "gaps": [
      {"gap": "<something STILL WRONG that needs raising — present tense>",
       "team": "<one of the nine team codes — the team that must pick it up>",
       "source_ref": "<the ticket, contact or case finding you read it from — REQUIRED>"}
    ],
    "guest_issues": [
      {
        "issue": "<one-line title, max 12 words, plain words, no trailing period>",
      "review_side": "<one line: what the guest said publicly about this | null>",
      "case_side": "<one line: what the Zendesk case shows — what they asked for, what we did, how it ended | null>",
        "claim": "<the guest's VERBATIM words from the review, quoted exactly | null>",
        "claim_accuracy": "<Accurate | Partly accurate | Inaccurate | Unknown>",
        "claim_accuracy_note": "<one sentence: how the evidence gets you to that verdict>",
        "root_cause": "<what broke — the mechanism | null>",
        "operational_failure": "<why it broke — why that mechanism could fail | null>",
        "sop_gap": "<why nothing caught it — the control that should have | null>",
        "pattern": "<counts and window from INSIGHTS | null>",
        "fix": {
          "action":   "<what to do>",
          "owner":    "<GUEST | SP | CONTENT | CO | TECH | INVENTORY | PRODUCT | BIZ | FINANCE — the SAME nine as flags[].team>",
          "because":  "<the gap this fixes, restated from the evidence>",
          "source":   "<booking | bms | zendesk | insights | dss | exp-page>"
        },
        "evidence": [
          {
            "text": "<what the record says, at its own grain — no source prefix, no URL>",
            "source": "<booking | bms | zendesk | insights | exp-page>",
            "time": "<DD Mon HH:MM — when the thing this records HAPPENED | null>",
            "ref": "<record URL or ZD-xxxxx | null>",
            "backs_claim": "<Yes | No | null>"
          }
        ]
      }
    ]
  },
  "support_interaction_notes": [
    {
      "zd_ref": "<ZD-xxxxx — the ticket this note is about; this is the join key | null>",
      "time": "<DD Mon HH:MM — when the GUEST reached out | null>",
      "channel": "<the support type: chat | email | call | web | app | null>",
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
    { "team": "<GUEST | SP | CONTENT | CO | TECH | INVENTORY | PRODUCT | BIZ | FINANCE>",
      "flag": "<one line: what went wrong that someone must act on>",
      "evidence": "<the fact that proves it>",
      "zd_ref": "<ZD-xxxxx | null>" }
  ],
  "area_of_improving": [
    {
      "point":  "<one short pointer, one line, no paragraph>",
      "from":   "<operational_failure | sop_gap | flag>",
      "source": "<the text of the failure, gap or flag it derives from, quoted from that field>"
    }
  ],
  "resolution": "<what the guest actually got: refund / comp / explanation, with amounts>",
  "suggested_response": "<the reply to the guest, 4-6 SHORT SENTENCES (~120 words): apologise, state what went wrong in plain words, state the remedy with its reference, close warmly. No internal jargon, no BID, no team names>",
  "takedown": { "verdict": "<Yes | No | Untraceable>" },
  "dss": {
    "prescribes": "<what the matched DSS row prescribes for this scenario>",
    "ref": "<DSS row URL | null>",
    "followed": "<followed | not_followed | unestablished — ONLY where the guest contacted support BEFORE the review; null otherwise>",
    "missed_next_step": [
      {"team": "<one of the nine team codes>",
       "action": "<the next escalation step the DSS row prescribes that DID NOT happen on this booking>"}
    ]
  }
}

## OUTPUT RULES — these are hard constraints, not preferences

1. Return ONLY the JSON object. No markdown fences, no commentary, no trailing explanation.
2. Every field in the template must be present. Use null for unknown or absent — never the
   strings "Unknown", "N/A", "TBD", "-", "?", "none" or an empty string in any field except
   where an enum explicitly lists that value.
3. `claim_accuracy` MUST be exactly one of: Accurate, Partly accurate, Inaccurate,
   Unverifiable, Unknown.
   UNVERIFIABLE AND UNKNOWN ARE DIFFERENT ANSWERS, and the difference decides what
   the reader does next. UNVERIFIABLE means you CHECKED and no record can settle it —
   the guest says the room was cold, the guide was rude, the queue was two hours;
   nothing we hold records any of those. That is finished work, and the note says
   which sources you looked in. UNKNOWN means you could not establish it — the lookup
   failed, the ticket was not retrieved, you ran out of evidence to check against.
   That is work OUTSTANDING.
   Never use Unverifiable as a fallback for "I did not look". Claiming a check you
   did not run is worse than admitting the gap, because nobody will go back to it.
   Nothing else, no punctuation, no trailing explanation. Put your reasoning in
   `claim_accuracy_note`. Do NOT write "Partially True — booking status shows…" in the verdict.
4. `claim` is the guest's own words copied from the review, inside no quote marks (the UI adds
   them). Never paraphrase. If the review does not state this issue in the guest's words, use
   null — which is normal for an issue the CASE surfaced and the review never mentioned.
   `review_side` is one line summarising what the guest said publicly about this issue, or
   null. `case_side` is one line on what the Zendesk case shows — what they asked for, what we
   did, how it ended — or null when they never contacted us about it.
5. Every analytical statement attaches to the issue it explains. `operational_failure`,
   `sop_gap`, `pattern` and `fix` are fields ON each guest issue. Do NOT emit document-level
   `what_happened`, `root_causes`, `operational_failure`, `sop_gap`, `pattern` or `fixes` lists.
   `owner` names the internal team that must ACT on this issue. When the claim is Inaccurate, or
   when no internal team is at fault, `owner` is null — never "Guest", "Customer", "None" or
   "N/A". A guest cannot be assigned work, so naming one as owner puts a party who will never
   see this RCA on the hook for the fix.
6. `evidence[].source` and `.ref` are structured fields. The `text` must contain no `[booking]`
   or `[insights]` prefix and no URL — put the identifier in `ref` and the origin in `source`.
6a. `ref` IS NOT OPTIONAL WHEN ONE EXISTS. `source` names a system; `ref` is the row in it, and
   without it the reader has been told where to look and not what to open.
     source "zendesk"  → the ZD-xxxxx the fact came from. Every timeline event and every raw
       ticket body above carries its ticket id, so this is always available. `null` here means
       you did not take it from a ticket.
     source "insights" → the window label the count covers, e.g. "90 days before 2026-08-04",
       exactly as INSIGHTS states it. A count with no window is not checkable, and the window
       is the thing that changes underneath it.
     source "booking" / "bms" → the booking id.
     source "exp-page" → null is correct; the page is the product config, not a row.
   `null` is a claim that no identifier exists, not a shortcut for not looking one up.
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
9. One guest issue per distinct problem on this booking — from the REVIEW, from the
   ZENDESK CASE, or from both. If the guest raises three things in the review, return three
   objects; if the case shows a fourth problem the review never mentions, that is a fifth
   object. Do not merge them, and do not invent one.

   THE REVIEW IS NOT THE ONLY SOURCE OF ISSUES, and treating it as one was the fault this
   rule used to have. A guest who asked to move their booking, was refused, and then wrote a
   review about "strict policy" has ONE story, and the review is its ending. An RCA that
   starts and stops at the ending explains nothing.

   EVERY ISSUE CARRIES BOTH SIDES, and either may be null:
     `review_side` — what the guest said publicly, in their own words where they said it.
     `case_side`   — what the Zendesk case shows: what they asked us for, what we did,
                     whether it was resolved. Read the ticket chronologically to write it.
   Null on one side is a FINDING, not a gap to hide:
     review_side null  -> the case shows a problem the guest never wrote about publicly.
     case_side null    -> they never contacted support; the review IS the case, and it is
                          an open-and-shut one. Say so rather than implying we looked and
                          found nothing.
   Where both exist and they DISAGREE, that gap is the most useful thing on the card. Write
   both plainly and let heading 3 explain how one became the other.
   Splitting a cause from its consequence is inventing one: "we did not disclose the delivery
   window" and "the delivery window clashed with their schedule" are one complaint, and the
   consequence belongs in that issue's `root_cause`, not in an issue of its own.
   Two checks that catch a bad split, both of which you can run on your own draft before
   returning it. (a) An issue's `operational_failure` must describe conduct by the team named in
   its `owner`. If you have written owner "RO" and an operational_failure about what CE did, the
   issue belongs to CE — or it is the same issue as one you have already written for CE, and
   should be merged into it. (b) If an issue's `root_cause` restates another issue's finding,
   that issue is the other one's consequence. Merge it.
   Every entry must trace to something that happened TO THIS GUEST — in the review, or in
   what they asked us for and what we did about it. It does not have to be in the review.

   WHERE A REFUSED REQUEST GOES, because this is the line that used to send the whole story
   to `flags` and out of the RCA:
     The guest asked and we COULD NOT (policy correctly applied) -> it belongs under heading
       3, as what actually happened. It explains the review. It is NOT a flag: nobody did
       anything wrong.
     The guest asked and we COULD have and did not, for whatever reason -> that is a FLAG.
       Our conduct, and someone must act on it.
     The guest asked and we did it -> narrate it under heading 3. No flag.
   So the ask is ALWAYS visible on the card; only the avoidable miss becomes a flag.

   Our own process gaps that touched no guest request — an out-of-policy refund nobody asked
   for, a missed internal SOP step — remain flags and are not guest issues. `claim` is null only where the review implies
   the issue without words, or on a rule 13 routed-scenario coverage row; a `guest_issues` entry
   with no claim and no routed scenario behind it renders as a numbered guest complaint with an
   empty Claim block, and leadership reads it as something the guest said. They did not.
   Do not repeat the SAME SENTENCE in both. A guest issue may describe what the guest asked
   for and did not get, while a flag names our failure to do it — those are two statements
   about one event and both belong. What must not happen is the same wording twice.
9-case-findings. `case_findings` IS THE BOOKING'S WHOLE STORY, EVIDENCED — and it is NOT a
    summary of the support chat. `support_interaction_notes` already covers contact by contact:
    who, when, on which channel, what was said. If a line could sit in either, it belongs
    there and not here. This section is what the RECORDS show happened, and it starts at the
    booking, not at the first contact.
    COVER THESE, IN THIS ORDER:
      (a) WAS THE BOOKING FULFILLED AS EXPECTED? Tickets sent, on time, right date, pax and
          variant — or not. This has nothing to do with support and is the part most often
          missing.
      (b) WHY THE GUEST REACHED OUT, and with what. Their ask, not the channel.
      (c) WHAT WE DID about it. The action, not the transcript.
      (d) THE GAP, where there were several interactions — how long they waited against what
          we promised.
    One line each, with the `source` that shows it.

    A TICKET EVENT IS NOT A CASE FINDING. The events timeline above the card already
    lists every Zendesk comment with its time and its actor — that is its whole job. §1
    restating them puts the same events on the screen twice in two wordings, and the
    reader cannot tell which is the summary and which is the record, so both stop being
    read. Cards have come back with §1 holding eight rows that were the timeline again:

        "02 Aug 09:13 — agent replied asking for the booking reference"
        "02 Aug 11:40 — guest resent the confirmation email"

    Those are timeline rows. They belong to the timeline.

    (a) to (d) are FOUR LINES, one each, and §1 is normally four rows or fewer. If you
    are writing a fifth, sixth and seventh, you are transcribing rather than finding.
    The test: could this line sit on the events timeline with a clock time next to it?
    Then it IS a timeline row — leave it there. §1 says what the records SHOW, which is
    a conclusion drawn across them: "tickets were sent to the wrong email", not "at
    09:13 an agent said the tickets were sent".

    THE SAME RULE BINDS `evidence` ON EACH ISSUE, because those rows are merged into
    this section for rendering. Evidence that quotes a ticket comment verbatim arrives
    in §1 as one more copy of the timeline.

    A SOURCE YOU COULD NOT READ IS NOT A FINDING. When the booking was never matched, or
    Zendesk had nothing, or the experience page is unavailable, do NOT write a finding
    saying so — and above all do not write one PER source. Cards have come back reading:

        "No booking record exists for this guest."
        "No Zendesk contact exists for this guest."
        "No experience-page redemption data was provided."

    Three lines, one fact: we could not look. The confidence trail already states which
    lookup ran and which did not, in its own words, with the reason — that is its job and
    it does it whether you write these or not. Repeating it here fills the section that is
    supposed to hold what happened with a list of what we could not see, and a reader
    scanning four near-identical negatives learns nothing they did not learn from the first.

    If the records show NOTHING about this booking, return an EMPTY list. An empty §1 with
    the trail explaining why is honest and readable. Four sentences agreeing that we have
    no data is neither.

    `time` IS REQUIRED WHERE THE RECORD SHOWS WHEN IT HAPPENED, in the same "02 Aug 09:13"
    form the timeline uses. This section is ordered BY IT, and a finding without one cannot
    be placed — on a real card every finding came back with `time: null`, so §1 opened with
    an August payment and put the booking's own creation eighth. If you can write "sent 02
    Aug 09:13" inside the text, you know the time: put it in the field.

    Null ONLY where the record genuinely does not say when — a standing fact like an
    experience-page setting. Do not invent one to force an order; an undated finding sinks
    and the card says how many did.

    THE TIME IS WHEN THE EVENT HAPPENED, not a time the finding mentions. "Booking created
    for the 03 Aug 08:30 slot" happened on 21 Jul; its `time` is 21 Jul, not 03 Aug.

    THIS APPLIES TO `evidence[]` ENTRIES TOO, under the same rule. Evidence rows are
    rendered in THIS list beside the narrative points, so an evidence row with `time: null`
    sinks to the bottom of §1 no matter when the thing it records happened. On a real card
    every evidence row came back without one and four findings sat under "cannot be placed"
    while their own text read "reported at 15:36 on 02 Aug". If the time is in the text, it
    goes in the field.
    ── `gaps` — WHAT IS STILL WRONG, AND WHO HAS TO FIX IT ────────────────────────
    An array on `what_went_wrong`: [{"gap": ..., "team": ..., "source_ref": ...}].

    A gap is something UNSOLVED that needs raising with a team. "Chat miss — the guest
    asked to revert to 08:30 and nobody followed up" → CO. "Krakville's escalation email
    is not populated" → SP. Present tense, because a gap that has been closed is not
    something anyone has to pick up.

    IT IS NOT what happened ("No one was aware of the vendor's time change" — that is a
    case finding), and NOT a recommendation ("Require an agent to contact the guest
    proactively" — that is a fix, and §3 holds those).

    GAPS COME AT TWO GRAINS AND YOU OWE BOTH. On a real card only the process grain came
    back and THIS GUEST'S unfinished business was missing entirely:

      THIS CASE   what is still outstanding for THIS booking and THIS guest. "The guest
                  asked to revert to 08:30, was told the window had closed, and the chat
                  ended with the problem unsolved — nobody went back to them" → CO.
      THE PROCESS the hole behind it. "No process requires RO to confirm the new
                  operator's pickup time with the guest" → CO.

    START WITH THE CASE GRAIN. It is the one that gets skipped, because the process
    statement feels like the more serious finding — and a team reading only the process
    gap does not know a specific guest is still waiting.

    A GUEST PROBLEM UNSOLVED AT CASE CLOSE IS ALWAYS A GAP. Being answered is not being
    solved: an agent explaining that nothing can be done leaves the guest exactly as
    stuck as before, and a case closed in that state has something outstanding whatever
    the disposition says. Goodwill paid afterwards does not close it either — a wallet
    credit is compensation, not the thing the guest asked for.

    EVERY GAP CITES WHERE YOU READ IT, AND `source_ref` IS A REFERENCE, NOT A DESCRIPTION
    OF WHERE TO LOOK. One of these, and nothing else:

      a ticket        ZD-34335318
      a booking id    32885089
      a case finding  quote its words, verbatim, from `case_findings`

      NO   "the chat transcript", "the support history", "the exp page"
      YES  ZD-34335318

    A gap with no source is DROPPED and counted, and that is deliberate: a process
    improvement that is generally true is not something this case surfaced. Do not raise
    a gap because it would be good practice. Raise it because the data in front of you
    shows it happening and shows nobody fixing it.

    If the case shows no unsolved gap, return an empty array. That is a real answer and
    the card says so.

    THIS SECTION CARRIES TWO KINDS OF POINT AND THEY DO DIFFERENT JOBS. Keeping them
    separate is what stops this section repeating itself:

      NARRATIVE points — (a) to (d) above. What happened, in order, from the booking.
      EVIDENCE points  — written on an ISSUE, in `evidence`. Each one PROVES OR DISPROVES
                         ONE CLAIM the guest made, and nothing else.

    AN EVIDENCE POINT IS NOT A TIMELINE ROW. "Rebooking sent 02 Aug 09:11; confirmation
    emailed 09:13" is a timeline row: it recounts. Evidence ADJUDICATES — the guest says the
    new time was never communicated, so the evidence is what the record shows about whether
    it was communicated, and it cites the ticket it was read from. If a point does not settle
    something the guest asserted, it is not evidence; leave it out or make it a narrative
    point.

    NEVER WRITE THE SAME FACT AS BOTH. A narrative point and an evidence point describing the
    same moment in different words is the repetition this section is built to avoid — it is
    the commonest way this card goes wrong, and rewording does not make it two facts.

    THE SUPPORT TICKETS ARE THE SOURCE for (b) and (c). A guest does not contact us without a
    reason: read the tickets for what they actually asked, what we replied, and whether their
    problem was solved. "Guest contacted support" is not an answer to (b).
    DO NOT WRITE A CLOCK TIME INTO `text`. Put it in `time`; the card does not render it and
    the events timeline is where the reader goes for the record with times on it. A finding
    that reads like a timeline row is the duplication this section keeps being accused of.
    AN EMPTY LIST IS A REAL ANSWER and the card says so in words. What must never happen is a
    case nobody read coming back looking like a case that was read and was clean.
9-fixes. `fixes` IS ITS OWN SECTION, one row per remediation, each naming the ONE team that
    must do it. Actions Taken is built from exactly this array, so a fix with no owner reaches
    the Unrouted tab and nobody picks it up. The team is whoever DOES the work — a refund that
    has to move is FINANCE even when the failure that caused it was CONTENT's.
10. `flags` contains failures only — things a named team must act on. An empty array means
    everything was checked and nothing needed raising; return `[]`, not a placeholder entry.
10-source. FLAGS AND FIXES COME FROM THE CASE HISTORY AS WELL AS THE REVIEW, and the case is
    the larger source of the two. The review is what one guest chose to write publicly; the
    Zendesk history is every ask, every reply, every internal note and every thing we did or
    did not do. Read it for failures in its own right, not only to corroborate the review:
      a request that sat unanswered past what we promised;
      an escalation to SP or Tech that the case shows was needed and never raised;
      a macro or DSS path applied to the wrong scenario;
      a fulfilment or refund the case shows we said we would do and no record shows we did;
      a content, catalog or inventory fault visible in the ticket that nobody flagged.
    None of those needs a sentence in the review to be real, and a case whose history shows one
    is not a clean case because the guest wrote about something else.
    SAY SO WHEN IT IS CLEAN. If you read the case history and it raised nothing, that is a
    result and `flags` is `[]`. What must not happen is a case history nobody read coming back
    looking the same as one that was read and was fine.
10-fix-owner. EVERY FIX NAMES ITS OWNER, from the nine teams in 10-teams, and the owner is the
    team that must DO the fix — not the team the guest complained about and not the team that
    owns the surrounding issue. A refund that has to move is FINANCE even when the failure that
    caused it was CONTENT's. An unowned fix cannot be placed in Actions Taken: it is reported
    as unrouted, which is a row nobody picks up.
10-teams. `team` IS ONE OF THESE NINE, and nothing else. They are the teams work is actually
    raised with, and Actions Taken is built by joining them: a step the DSS guidelines prescribe
    is only raised where a flag names the same team, so a team spelled any other way raises
    nothing at all.
      GUEST      — NA/Guest error. The guest's own mistake, or nobody's: no team has work here.
      SP         — Supply Partner. The vendor: the guide, the venue, the tour, the meeting
                   point on the ground, anything claimed against them.
      CONTENT    — Content/Catalog/Media team. WHAT THE PRODUCT SAYS AND HOW IT IS CONFIGURED:
                   a missing or wrong VARIANT, PAX TYPE, INCLUSION or PAGE STATEMENT, a
                   voucher's redemption copy, a missing callout. "No Baby/Infant (<1.00 m,
                   free) pax type exists in the booking flow for TGID 20842" is CONTENT — the
                   flow renders whatever pax types the catalog defines, so a missing option is
                   a configuration fault, not a flow fault.
      CO         — CO team. The support desk itself: how the contact was handled, the macro
                   used, the DSS path taken, the follow-up missed, the refund or resend CO
                   owes the guest. Everything the old CE and RO chips carried.
      TECH       — Tech team. Headout's own systems failing: BMS, the ticket PDF, the
                   fulfilment automation, a vendor API, Selenium.
      INVENTORY  — Inventory Team. Stock and fulfilment ownership: IO on-call, prepurchase
                   inventory, a listing sold that was not available.
      PRODUCT    — Product team. THE FLOW, APP OR SITE FAILING TO DO ITS JOB WITH A CORRECT
                   CATALOG: checkout errors, a page that will not load, an app that crashes,
                   an offer the site advertises without its precondition. If the catalog entry
                   itself is wrong or missing, it is CONTENT and not this.
      BIZ        — Biz team. The commercial relationship and the escalation ladder: BDM,
                   BizOps, recurring patterns on a TID-VID, pricing and commercial terms.
      FINANCE    — Finance team. Money that has to move and the record that proves it: a
                   failed or stuck refund, an ARN, a chargeback, a bank transfer.
    Choose the team that must DO something. If none of the nine can act on it, it is not a
    flag — see 10a.
10a. EVERY FLAG MUST SIT ON A SUPPORT INTERACTION THAT ACTUALLY HAPPENED. A flag names
    something a person or a system did during a contact with THIS guest about THIS booking,
    and the contact has to be in the data above — a Zendesk ticket, a chat, an email, an SP
    exchange. Point at it: `zd_ref` carries the ticket, `evidence` carries what was said or
    done in it.
      NOT a flag: what nobody did because the guest never wrote in. No contact, no flag.
      NOT a flag: a general process improvement, a policy you would prefer, a pattern across
        other bookings. Those are `area_of_improving`.
      NOT a flag: something that happened on a different booking or a different guest's
        ticket, however similar.
      NOT a flag: the guest's dissatisfaction. That is the review, not a failure.
    A flag with no contact behind it cannot be checked by the team it is raised against, and
    they will spend the time proving a negative. If the only support record is one you cannot
    tie to this booking, raise nothing.
    THIS DOES NOT MEAN THE FINDING IS LOST. Rule 6 allows an operational failure with no
    contact behind it — a wrong meeting point, a mis-set field, a listing error can all fail a
    guest who never wrote in. That belongs on the issue as `operational_failure`, and its
    process fix in `area_of_improving`. It is only `flags` that is contact-bound, because a
    flag is a request for a named team to go and look at a specific exchange.
10b. `support_interaction_notes` — THE GUEST'S CONVERSATIONS WITH US, AND ONLY THOSE.
    One entry per contact the GUEST initiated or took part in, in the order they happened.

    WHAT COUNTS: chat, email, call, web, in-app — any channel where the guest and Headout
    actually spoke.
    WHAT DOES NOT, and these are the ones that pollute the section:
      * the BOOKING THREAD — Zendesk classifies it as a task, not a conversation. It is
        machinery: fulfilment runs, ticket dispatch, system mail. It belongs in
        `booking_logs`, and putting it here makes it look as though somebody talked to
        the guest when nobody did.
      * INTERNALLY RAISED TICKETS — a DSS follow-up, a bot ping, an ops-to-ops ticket. The
        guest was not on them.
      * THE REVIEW ITSELF. It is the artefact being analysed, not a channel they reached
        us on.
    Each one you wrongly include raises the contact count, and a count that is one too
    high reads as a guest who was handled when they were not.

    FOR EACH CONTACT:
      `time`     when the GUEST reached out. Chronological across the whole array.
      `channel`  the support type: chat / email / call / web / app.
      `summary`  the interaction itself, in concise single sentences.

    WHAT THE SUMMARY COVERS, in the order it happened:
      * what the guest reached out with — their issue, in their own terms;
      * what we replied. SKYLAR IS AN AI BOT, not an agent: where Skylar answered, say
        so, because "we replied in 30 seconds" means something entirely different when
        it was the bot;
      * what the guest said back;
      * whether the case was RAISED INTERNALLY, and whether DSS was followed.

    HOW TO KNOW WHETHER IT WAS RAISED INTERNALLY. Do not infer it from what we promised
    the guest. Look for it in the record: an INTERNAL NOTE on the ticket, or a Zendesk
    ticket opened by a bot against the SAME BOOKING ID. If one exists, say what was
    raised. If the record shows nothing, say nothing about it — an absence you did not
    verify is not a finding.

    `time` AND `channel` ARE READ OFF THE TICKET, NOT JUDGED. Where a Zendesk frame covers
    this contact the dashboard shows the FRAME's values, not yours — yours are used only
    for a contact Zendesk has no frame for, such as a call the guest describes. State them
    from the record where you have it and leave them null where you do not. Never write a
    time into the prose as a substitute for filling `time`.

    CONCISE BULLET POINTS, SINGLE SENTENCES, CHRONOLOGICAL. Factual and plain: what
    happened, not how it felt. No paragraphs.

    `detail` NARRATES. `ce_miss` JUDGES. NEITHER DOES THE OTHER'S JOB.
    `detail` is the account of the exchange and nothing else: what the guest asked, what
    we answered, what they said back — in their words and ours. It carries no verdict.
      ACCOUNT:  "Taylor replied that the Night Safari tickets stayed valid for 60 days."
      VERDICT:  "Taylor replied that the tickets stayed valid WITHOUT CHECKING whether a
                timeslot was already reserved."
    The second is the criticism wearing the account's clothes. It puts the same finding on
    the card twice — once unlabelled inside the narration, once as the miss — and the
    unlabelled copy is the one a reader takes for fact.
    So `detail` contains no "without checking", "failed to", "should have", "did not",
    "neglected", "incorrectly", "wrongly". If a sentence names a fault, it is a `ce_miss`.
    A contact where nothing went wrong has `ce_miss: null` and a `detail` that simply says
    what was said. Do not manufacture a miss to fill the field.

    IF YOU CANNOT DETERMINE SOMETHING, WRITE NOTHING FOR IT. A guessed escalation or an
    invented reply is worse than a blank — it reads as read off the record.

    IF THERE WAS NO DIRECT CONTACT AT ALL, return exactly one entry whose `summary` is
    "No direct interaction found between the customer and the support team." and every other
    field null. Do not pad the section with the booking thread to avoid an empty one.

11. If a section genuinely has nothing (no SP contact, no support contact), return an empty
    array. Do NOT fabricate a row whose summary says nothing was found. The REVIEW ITSELF is
    never a support contact: it is the artefact being analysed, not a channel the guest reached
    us on. Never emit a `support_interaction_notes` row for it, and never write "Trustpilot",
    "review" or "public review" as a `channel` — a phantom contact makes the contact count
    permanently one too high, and it reads as if someone handled the guest when nobody did.
12. THE ISSUE-SPECIFIC QUESTIONS ARE CHECKS TO WRITE AGAINST, NOT A SECTION TO FILL IN.
    There is no `issue_specific_answers` field and there is no section for them on the card.
    Work through every one against the backend before you write anything, and let the answers
    constrain what you then write: your verdict, your root cause and your SOP gap must all be
    consistent with them. A `claim_accuracy` of Accurate on an issue whose questions the record
    answers the other way is the failure this exists to prevent.
12a. ANSWER THEM FROM THE BACKEND, silently. What the record shows settles them, not what the
    guest says happened — their account is the claim, and the question asks what we can see.
    Where the record does not settle one, it is unsettled, and the honest place for that is
    `claim_accuracy: "Unknown"` with the note saying which source you looked in.
12b. WHEN A QUESTION SURFACES SOMETHING WE MISSED, WRITE IT WHERE IT BELONGS: as that issue's
    `operational_failure` if a person or system did the wrong thing, or as its `sop_gap` if
    nothing was in place to catch it. Decide which of the two it is — they are different
    findings and they go to different teams. Do NOT report it as an answer, a count, or a
    line about having run the check.
    A QUESTION WHOSE ANSWER IS "NO" IS NOT AUTOMATICALLY EITHER OF THOSE. It becomes one only
    when rule 6 is satisfied — the backend shows it, it belongs to this booking and this
    contact, and it matches what the guest actually complained about on that issue. A question
    answered No about a step nobody was required to take, or about something the guest never
    raised, is a check that came back clean. Write nothing for it.
13. Every scenario in SCENARIOS_ROUTED must be covered by at least one guest issue: its root
    cause and fix live on that issue. Do NOT emit a separate per-scenario block, and do not
    drop a routed scenario — if a routed scenario is not supported by the review or the data,
    return a guest issue for it with `claim_accuracy: "Inaccurate"` or `"Unknown"` and say why
    in `claim_accuracy_note`.
14. `dss.prescribes` states what the matched DSS row prescribes for this scenario, in its own
    words — it is reference data, not your analysis, so do not add whether we complied, and
    do not restate the row's L1/L2/sub-theme (the UI derives that from the
    classification). Do NOT cite the DSS sheet in `evidence[]` at all — it has no source
    value, and a row about the sheet's own coverage is not a record of what happened to this
    booking. `dss.prescribes` is where the needle belongs.
14a. `dss.followed` IS THE COMPLIANCE VERDICT, and it is a different field from `prescribes`
    for a reason: `prescribes` is what the sheet says and must stay free of your analysis,
    while this is whether we actually took that path.
    ANSWER IT ONLY WHERE THE GUEST CONTACTED SUPPORT BEFORE POSTING THE REVIEW. A guest who
    wrote in first has already been through a decision sheet — there was a prescribed path and
    we took it or we did not. A review with no prior contact has no such path: nobody was owed
    the step, so "not_followed" there is blame for something never required, and "followed" is
    praise for the same. Leave it null and the card says the check did not apply.
    `not_followed` is a FINDING. Say what the sheet prescribed and what was done instead, in
    the issue's `operational_failure` if a person or system departed from it, or its `sop_gap`
    if the path did not cover the case. Do not restate the verdict as its own bullet.
    `unestablished` when they did write in and the record does not show what path was taken —
    that is a real answer and is not the same as `not_followed`.
    THIS IS ALSO CHECKED IN CODE. The timeline decides whether the guest wrote in first, and a
    verdict written where they did not is dropped with a note on the trail; a case where they
    did and this was left null comes back as `unestablished`. Answer it properly here so the
    card is right the first time.
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
    Rule 9b applies to both: an apology written in corporate register reads as insincere to a
    guest, and a stated issue in internal shorthand is unreadable to everyone.
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
19. `suggested_response` IS THE APPROVED MACRO, adapted to this guest. The macro shown above was
    chosen for this review and gated so that what it promises is a remedy the DSS actually
    named — so its sentences are the approved ones and they are what goes out. Keep them.
    Do not rewrite it in your own words, do not compress it to a summary, do not use it only
    as a register to imitate.
    ADAPT IT, because a macro covers the general situation and this guest raised specifics:
    work in what they actually said, drop a paragraph that plainly does not apply to them, and
    fill every placeholder from the record. A reply that answers half their complaint reads as
    a form letter, which is usually what they are already angry about.
    NEVER GO BEYOND WHAT THE MACRO PROMISES. If it offers a credit, do not turn that into a
    refund; if it promises nothing, add no compensation of any kind. What it offers has been
    authorised for this case and anything past that has not — that is a policy decision, not a
    wording one. Every FACT in the reply still comes only from this case's evidence: never
    invent a figure, a date or a status to fill a placeholder the record cannot fill.
20. `area_of_improving` IS POINTERS, NOT A PARAGRAPH. One short pointer per array element, one
    line each, no semicolons, no "and also". It used to come back as a single paragraph welding
    five recommendations together, half of it material that appears in no finding on this card.
20a. EVERY POINT NAMES WHERE IT CAME FROM, AND THE NAME IS CHECKED. `from` is one of
    `operational_failure`, `sop_gap` or `flag`, and `source` is the text of that finding,
    quoted from the field it sits in on THIS card. This is not decoration and it is not for the
    reader: it is the constraint that makes an invented point impossible to write, because
    there is nowhere to put a source you do not have. A point whose `source` matches no
    operational failure, no SOP gap and no flag on this card IS DROPPED before it renders — the
    same way `fix` is null when no evidence entry shows a gap.
    So do not write the point first and hunt for a source afterwards. Read the failures, the
    gaps and the flags you have already written, and write the correction to one of them.
20b. NOTHING INVENTED, AND EMPTY IS AN ANSWER. This is the correction to a documented gap, not
    an opinion about a better world: no generic advice ("improve communication"), no policy you
    would prefer, no industry practice. Flags are a guide to what the failures missed, not
    licence to add material. If the card has no operational failure, no SOP gap and no flag,
    return `[]` — a padded section is worse than an empty one, because it is read as findings.
20c. THE POINT IS THE FIX, NOT THE FAULT. `source` already says what went wrong; the point says
    what to change so it does not happen again. "Tickets were sent late" is the source. "State
    the delivery window on the experience page before checkout" is the point."""

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
_BOOKING_DATE_KEYS = ("date_of_booking", "creationDate", "bookedOn",
                      "visitDate", "date_of_visit", "experienceDate",
                      "bookingDate")


def _readable_booking(bk: dict) -> dict:
    """The booking dict with its timestamps in a form a human wrote.

    <<BOOKING>> handed the model json.dumps(bk) verbatim, and BigQuery returns
    a TIMESTAMP as epoch seconds - so the model was shown
    "date_of_booking": "1.785791592E9" and wrote a timeline row whose time was
    that string. It cannot emit a clock time it was never given.

    Converted here rather than in the bookend alone, because the model builds
    booking-created events from this dict as well as from the bookend, and the
    bookend fix left those rows showing a bare date.
    """
    out = dict(bk or {})
    for k in _BOOKING_DATE_KEYS:
        v = out.get(k)
        if v in (None, ""):
            continue
        pretty = _fmt_date_ist(v)
        # Only when it actually read as a date. "unreadable timestamp" and the
        # untouched original both mean we could not parse it, and replacing a
        # value with a phrase would hand the model a sentence to copy into a
        # time field.
        if pretty and pretty not in ("unknown", "unreadable timestamp") \
                and pretty != str(v):
            out[k] = pretty

    # The SP escalation email is NOT shown to the model, by request. It is a
    # contact address, not analysis, and feeding it (or a status derived from
    # it) let the model write findings about whether the SP "could be emailed" —
    # statements the RCA reader has no way to act on. All escalation-email keys
    # are dropped here so nothing about it can reach generated RCA text; the
    # value still flows to the UI's own data (client) independently of this.
    for _k in ("escalationEmail", "escalationEmailSource", "escalationType",
               "escalation_email_status"):
        out.pop(_k, None)
    return out


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


def prompt_stamp_state(stamped) -> str:
    """"current" | "stale" | "unstamped", for a draft's rca_prompt_version.

    WHY THIS IS NOT A ONE-LINE COMPARISON AT EACH CALL SITE. Every diagnostic
    reads a STORED draft and reports it against the rules in the code running
    now. When the two disagree the report is about a card that no longer
    exists, and it looks exactly like a report about a card that does.

    That cost three round trips on one review. `gaps` came back `[]` and the
    trace said "the model was asked and found no unsolved gap" — a sentence
    that is true of a clean case and false of a draft written before `gaps`
    was in the schema at all. Same two words on screen, opposite meanings, and
    the only thing separating them is this stamp.

    Content-addressed, so it moves whenever the prompt body moves. That is
    what makes "stale" mean something narrower than "old": the model was asked
    different questions, so its answers cannot be read against these rules.
    """
    s = str(stamped or "").strip()
    if not s:
        return "unstamped"
    return "current" if s == RCA_PROMPT_VERSION else "stale"


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
        # BigQuery hands a TIMESTAMP back as epoch seconds, and a float that
        # large str()s into scientific notation. "1.785752592E9" reached the
        # timeline as the booking-created time and rendered verbatim — a
        # 13-character machine number where a date belongs, which reads as a
        # broken row rather than as a booking created on 2 Aug.
        #
        # Seconds, not milliseconds: bounded to a plausible window rather than
        # by digit count, because both units are 10-13 digits at different
        # points in history and guessing wrong is 50 years out, silently.
        _num = None
        try:
            _num = float(s)
        except (TypeError, ValueError):
            pass
        if _num is not None:
            if _num > 1e11:              # milliseconds
                _num /= 1000.0
            if 1e9 < _num < 4e9:         # 2001-2096, i.e. a real booking
                return (datetime.fromtimestamp(_num, timezone.utc)
                        .astimezone(IST).strftime("%d %b %H:%M IST"))
            # A number outside that window is not a timestamp we can read.
            # Returning it unchanged put "1.78E9" on the card; saying so puts
            # a fact there instead.
            return "unreadable timestamp"
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
   - Review posted - NOT necessarily last. Emit it with its own timestamp and
     let the sort place it. It used to be a mandatory FINAL bookend, which put
     a review that was published BEFORE a later CE reply after that reply on
     the card — a false order, and one that matters now the publish date comes
     from the Trustpilot payload rather than from the moment Slack relayed it.
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

   NEVER COLLAPSE AN EVENT WITH is_internal: true INTO ONE WITHOUT IT, and
   never the reverse. An internal note is what Headout wrote to itself about
   this booking - the reschedule that failed, the escalation, the credit - and
   folding it into the confirmation mail beside it destroys the only record of
   it. Measured on one booking: 26 comments, 18 of them internal notes
   carrying the reschedule and the refund, arrived here and 8 rows came back.
   The notes were not dropped by any filter; they were collapsed away.

   Two internal notes MAY collapse together when they are the same automated
   line repeating - that is what "one action at one moment" means for them.

4. AN INTERNAL NOTE THAT RECORDS SOMETHING THAT HAPPENED is never keep: false.
   A reschedule, a cancellation, a refund, a credit, an escalation, an agent's
   own note about the booking — those are the record of what we DID about the
   problem, and they are the rows most often lost because they read like
   machinery.

   THE TICKET'S OWN FURNITURE IS NOT AN EVENT and does not belong on a
   timeline: "Support history thread opened", a Booking Info or ITINERARY
   MARGIN dump, a Booking Details field snapshot, a chat-session header. They
   describe the ticket, not the booking. Set keep: false on those.

   The distinction is what happened to the GUEST'S BOOKING, not what happened
   to the ticket about it.

5. LABELS - a DESCRIPTIVE line saying what this event actually was, written
   from its own body. Not a category.

   There used to be a closed vocabulary of ten here, and it made every card
   read the same: "SP response" tells a reader that a partner said something,
   not what they said or what changed. "Booking details posted" is the name of
   a mechanism, not of an event. The reader is scanning for the moment the
   booking went wrong, and a column of ten repeating nouns hides it.
   THESE ARE THE SHAPE, NOT THE WORDS. A run came back with "Booking
   intimation sent to the supply partner" copied verbatim from this table, and
   with "Booking details posted", "Booking status snapshot posted", "Support
   history thread opened", "Credit refund comment logged" — mechanism names,
   which is exactly what the table exists to replace. Write the label from
   THIS event's own body. If it could sit unchanged on another booking's
   timeline, it is a category and not a label.

     INSTEAD OF          WRITE
     SP response         Booking intimation sent to the supply partner
     Tickets sent        Confirmation email sent to guest
     CE response         Apology and refund promised to guest
     Guest reached out   Guest asked to move the tour to 14 Aug
     Booking cancelled   Original booking cancelled via API
     Refund issued       Full refund of CHF 461.19 processed
   Keep them short — a line, not a sentence, and no trailing period. UNDER 120
   CHARACTERS: past that the row clips and the reader is shown the cut marker
   instead of the end of your label.

   THE ACTOR RULE IS UNCHANGED AND OUTRANKS ALL OF THIS. A label naming
   someone who did not act is a false statement about a person, and it is the
   one error here that can end up quoted back to a customer. A guest event
   describes what the GUEST did; a CE event what WE did; an SP event what the
   PARTNER did. Descriptive does not mean re-attributed.
   THE LABEL MUST MATCH THE ACTOR. This is a CONSTRAINT ON WHO THE LABEL MAY
   SAY ACTED, not a list of words to use. A label naming someone who did not
   act is a false statement about a person, and it is the one error here that
   can end up quoted back to a customer.

   THIS USED TO BE A CLOSED VOCABULARY — "CE response", "SP response",
   "Tickets sent", "Guest reached out" — introduced as an ANTI-attribution
   rule and prefaced "outranks all of this". It therefore cancelled the whole
   descriptive-labels rule twenty lines above it: the model obeyed the
   outranking rule and every card came back with the same six nouns the table
   above tells you not to write. Say what happened, and obey the attribution
   constraint while you do it.
     actor "guest"   -> what the GUEST did, in their terms: "Guest asked to
                        move the tour to 14 Aug", "Guest chased for tickets".
                        Never write that the guest contacted, asked, replied or
                        complained unless this event IS the guest's own words.
     actor "co"      -> what WE did: "Apology and refund promised to guest",
                        "Guest told tickets would arrive by 18:00".
     actor "sp"      -> what the PARTNER did or was told: "Booking intimation
                        sent to the supply partner", "Partner confirmed pickup
                        time".
     actor "system" / "ai" -> the machine action AND ITS OUTCOME: "Fulfilment
                        run failed", "Confirmation email sent to guest",
                        "Credentials generated". Name the specific machine that
                        ran: a fulfilment attempt is "Fulfilment run ...",
                        never the name of the email beside it.
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

4b. NEVER NAME A VENDOR OR PRINT AN EMAIL ADDRESS. The supply partner's
   trading name and any address are internal identifiers and they crowd out
   the fact the row exists to carry. Write "the supply partner" or "the
   vendor".
     WRONG: "Booking intimation sent to EMILIAN STACHURA; 2 Adults"
     RIGHT: "Booking intimation sent to the supply partner; 2 Adults"
   The vendor REFERENCE (ref HEA-97947961) is not a name and stays: it is what
   someone uses to find the booking on the partner's side.

4c. SAY IT ONCE. A fact already carried by an earlier row is not repeated in a
   later one. The cancellation policy is the worst offender — it was on every
   row of one real timeline — and it is not a timeline event at all: it is a
   property of the booking and it belongs in Booking details. Do not write it
   on any row unless the policy CHANGED at that moment, which is an event.
   The same applies to pax, price and pickup point: state them where they are
   established, not on every row that mentions the booking.

4d. INTERNAL NOTES - keep the ones that record something that happened to the
   BOOKING; drop the ones that only say how to handle the TICKET.
     KEEP  "[RESCHEDULE] Automation has failed for booking 32885089; please
            handle manually" - the reschedule did not go through. That is the
            case.
     KEEP  "NAR, tix are already rescheduled for +45 mins" - a disposition
            instruction wrapped around a real outcome. Write the OUTCOME:
            "Reschedule confirmed at +45 minutes", not the instruction.
     DROP  "Please close this ticket once the guest confirms" - ticket admin.
            Nothing happened to the booking.
     DROP  assigning, moving to pending, adding a tag, picking a macro, SLA
            reminders, signature blocks, empty comments.
   WHEN YOU CANNOT TELL, KEEP IT. A kept row is visible and can be argued
   with; a dropped event cannot be recovered.

4e. REPEATED AUTOMATED MESSAGES COLLAPSE INTO ONE ROW WITH A COUNT AND A SPAN.
   The repetition is the signal; the individual lines are not. Four rows
   saying one thing push the events that matter off the screen.
     NO   02 Aug 09:14 system Reschedule cannot be pushed to Pending
          02 Aug 15:22 system Reschedule cannot be pushed to Pending
          02 Aug 19:02 system Reschedule cannot be pushed to Pending
          03 Aug 10:07 system Reschedule cannot be pushed to Pending
     YES  02 Aug 09:14 system Reschedule blocked, 4 system pings
                              Blocked pending SP action; 09:14 on 02 Aug to
                              10:07 on 03 Aug.
   Same for repeated automation attempts: one row, the count, the window. This
   is NOT rule 3's collapse — that one is for a single action recorded twice
   at one moment. This is one message recurring over hours, and the span is
   the point.

6. SUMMARIES - NOT sentences. 2-3 telegraphic phrases separated by "; ",
   each phrase one fact. Aim for about 100 characters, but FINISH THE
   THOUGHT: a complete third phrase at 130 characters beats a phrase that
   stops halfway at 100. Never trail off, never abbreviate a word to fit.
   If it will not fit, drop a whole phrase rather than truncate one.
   Drop articles, subjects and connective prose; keep numbers, names,
   dates and outcomes:
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

7. ORDER - Booking created first, then the events as given. The input is
   already in order; do not re-sort it.
   REVIEW POSTED IS NOT LAST. It carries its own timestamp and the card sorts
   on it. This rule used to say "Review posted last", which contradicted the
   bookend rule above outright and put a review published BEFORE a later CE
   reply after that reply — a false order, and the reason the publish date is
   read from the Trustpilot payload at all.

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


# ─── DSS scenario selection (AI, replaces the keyword selector) ─────────────
def dss_scenario_select_prompt(situation: str, candidates: list,
                               value_usd=None, is_partnered=None,
                               experience: str = "") -> str:
    """Ask the model to pick the DSS scenario row that matches the guest's
    situation — semantically, not by keyword overlap — or none.

    `candidates` is [{"i": int, "scenario": str, "action": str}, ...], already
    hard-filtered (public review, CE/RO, partnered where it applies). The model
    chooses ONLY from these; it never invents a scenario or a prescription. The
    guest's wording will not match the sheet's wording verbatim — that is the
    whole reason this replaced the keyword match — so judge by what actually
    happened, not shared words.
    """
    rows = "\n".join(
        f'[{c["i"]}] SCENARIO: {c["scenario"]}\n     PRESCRIBES: {c.get("action","")}'
        for c in candidates)
    ctx = []
    if experience:              ctx.append(f"Experience: {experience}")
    if value_usd is not None:   ctx.append(f"Booking value: ${value_usd} USD")
    if is_partnered:            ctx.append(f"Partnered vendor: {is_partnered}")
    ctx_block = ("\nContext:\n  " + "\n  ".join(ctx)) if ctx else ""
    return f"""You are matching a guest's situation to the correct DSS scenario.

Below is the guest's situation, then a NUMBERED list of candidate scenarios from
the decision sheet. Pick the ONE scenario whose meaning best fits what happened
to this guest. The guest will not use the sheet's exact words (e.g. "I had a
medical emergency" matches a "medical grounds / bereavement" scenario) — match
on MEANING, not shared keywords.

RULES:
- Choose ONLY a number from the list. Do not invent a scenario or a resolution.
- If NONE of them genuinely fits, return index -1. Do not force a weak match.
- Booking value may matter for some scenarios; use it as context, but a
  value-threshold judgement is the associate's call — do not exclude a scenario
  just on value.
- Output STRICT JSON, nothing else:
  {{"index": <number or -1>, "confidence": "high"|"medium"|"low", "reason": "<one line>"}}

GUEST SITUATION:
{situation}
{ctx_block}

CANDIDATE SCENARIOS:
{rows}

JSON:"""


def reply_macro_select_prompt(review_text: str, candidates: list,
                              l1: str = "", l2: str = "", sub_theme: str = "",
                              dss_action: str = "") -> str:
    """Ask the model to pick the approved macro whose SCENARIO matches this
    guest's situation — by meaning, not by shared words.

    `candidates` is [{"i": int, "situation": str, "promises": [str, ...]}, ...],
    already gated on the remedy the DSS named (see services/reply_macro.py), so
    every option here is one the playbook permits. The model chooses the
    scenario; it never chooses the remedy, because that gate has already run and
    offering an unauthorised one is not a judgement call.

    L1/L2 ARE HINTS, NOT THE KEY. They are frequently wrong or absent — the
    whole manual-review cascade came from treating a missing L2 as decisive — so
    the model is told to read the review and deduce which of the listed themes
    it belongs to, using the classification only as corroboration.
    """
    rows = []
    for c in candidates:
        promises = c.get("promises") or []
        offer = (f"  [offers: {', '.join(promises)}]" if promises
                 else "  [offers nothing — acknowledgement / information only]")
        rows.append(f'[{c["i"]}] {c["situation"]}{offer}')
    rows_block = "\n".join(rows)

    ctx = []
    if l1:        ctx.append(f"Classified L1: {l1}")
    if l2:        ctx.append(f"Classified L2: {l2}")
    if sub_theme: ctx.append(f"Sub-theme: {sub_theme}")
    if dss_action:
        ctx.append(f"DSS prescribes: {dss_action[:600]}")
    ctx_block = ("\nContext (corroboration only — the review decides):\n  "
                 + "\n  ".join(ctx)) if ctx else ""

    return f"""You are choosing which APPROVED reply macro fits a guest's review.

Below is the guest's review, then a NUMBERED list of macro scenarios. Pick the
ONE whose scenario best describes WHAT HAPPENED TO THIS GUEST.

HOW TO CHOOSE:
- Match on MEANING. The guest describes their experience in their own words and
  will not use the macro's vocabulary. "We stood at the door for an hour and
  nobody came" is a meeting-point/no-show scenario however it is phrased.
- Read the review first and decide which of the listed themes it belongs to.
  The classification below is corroboration, not the answer: it is often absent
  and sometimes wrong, so never pick a scenario only because its words resemble
  the L1/L2 label.
- Every option listed is already permitted for this case. What each one offers
  the guest is shown so you can prefer the one that fits what actually
  happened — but do NOT reject a scenario because you would have offered
  something different. That decision has already been made.
- If NONE of the scenarios genuinely describes this guest's situation, return
  index -1. A near-miss macro sent to a guest is worse than no draft: it answers
  a complaint they did not make.

Output STRICT JSON, nothing else:
{{"index": <number or -1>, "confidence": "high"|"medium"|"low", "reason": "<one line>"}}

GUEST REVIEW:
{review_text}
{ctx_block}

MACRO SCENARIOS:
{rows_block}

JSON:"""
