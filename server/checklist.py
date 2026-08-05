"""
Baked-in RCA checklist — verbatim from Brief v7.1.
No runtime fetch. No Google Sheets dependency.
"""
import re

# Mandated structure for the "What went wrong" section (Headout ORM).
# Headings 1–5 are ALWAYS included; the (a)/(b)/(c) sub-points are indicative —
# use only the ones relevant to the case. Objective: concise, structured, and
# focused on the operational failure, NOT a restatement of the customer's review.
WHAT_WENT_WRONG_STRUCTURE = [
    "1. Guest issue — brief 1–2 line summary (concise pointers) of the issue the guest experienced.",
    "2. Is the guest's claim accurate? — state one of: Yes / Partially True / No.",
    "3. What actually happened? — (a) root cause; (b) operational failure, if any; (c) SOP/process gap, if any.",
    "4. Supply Partner escalation — (a) did CE escalate to SP? Yes / No / N/A; "
    "(b) if we are not escalating, specify why (e.g. SP is on DND).",
    "5. Fixes — (a) tag the relevant team(s)/stakeholder(s) who need to evaluate or address the identified gaps; "
    "(b) briefly mention any corrective actions taken or proposed.",
]

GENERAL_GUIDELINES = {
    "rca_output": [
        "Fill every field: insights, booking details, what went wrong, customer/SP interactions",
        "No internal jargon — complete, neutral sentences",
        "Raise every issue found during investigation",
        "Bulleted format, not paragraphs",
        "Tag relevant teams for any underlying/major experience issue",
        "Do NOT offer DSS — we add them or give a partial refund",
    ],
    # How the AI must structure the "What went wrong" section.
    "what_went_wrong_structure": WHAT_WENT_WRONG_STRUCTURE,
    "response": [
        "Follow org-wide communication guidelines and Headout tone",
        "Address every issue raised in the review",
        "Use the correct macro — do not freehand; use Support copy AI for edits",
    ],
    "interaction": [
        "Describe each interaction briefly and name the gap",
        "Give date/time for each touchpoint",
        "State explicitly if no comms were found",
    ],
}

CE_ERROR_CHECKS = [
    "Delayed response to guest",
    "2+ non-autoresolved queries from the guest",
    "Guest query not addressed / no response given",
    "Missed follow-ups or deadline crossed",
    "Inappropriate tone / lack of empathy",
    "Incorrect DSS/SOP application",
    "Too many back-and-forths without resolution",
    "Asked guest to add credits instead of doing it directly",
    "Did not check clarity or internal notes",
    "Did not escalate to SP / Escalation / Biz / IO",
    "Used incorrect macros",
    "Missed content/catalog/inventory issues",
]

RO_ERROR_CHECKS = [
    "Tickets sent within expected timeframe (payment receipt, experience page)",
    "Tickets match booking: date, time, pax, experience, variant (Manual FF)",
    "Vendor API issues raised with tech team",
    "Voucher on BMS checked",
    "FF-type tagging correct — Vendor API <10min→inv-ops, >10min→RO; Selenium <30min→selenium-oncall else RO; Manual/PP/Free Sale→RO",
    "Comms with guest clear and timely",
    "DSS/SOP applied correctly by RO",
    "RO checked clarity and reviewed internal notes",
    "RO escalated to SP where required",
    "RO escalated to Escalation/Business/IO where required",
    "Content/catalog/inventory issues identified and flagged",
    "CE pings actioned or followed up",
    "Booking instructions followed",
]

SCENARIO_CHECKS = {
    "Tech error during booking": [
        "Check clarity for errors and proof (e.g. wrong date booked)",
        "Check URL and booking flow on Zendesk + experience page; if unsure raise with tech",
        "If clarity fails, raise with tech team",
        "Action as per DSS",
        "Confirm FF was done correctly; check for CE/RO error",
    ],
    "Redemption issue with tickets": [
        "Is it fulfillment, tech, or operational (denied entry / invalid tickets)?",
        "Tickets sent on time (payment receipt)?",
        "Ticket details valid: date, time, pax, experience, type?",
        "Raise with Tech for BMS/PDF/app issues",
        "Raise with Inventory/Business/SP by FF type (prepurchase→IO, freesale→SP/Biz)",
        "Did other bookings face the same issue / any ongoing internal issue?",
        "If major/recurring, raise with Escalation team",
        "Can we resend tickets or refund? Action per DSS",
    ],
    "AG redemption issues": [
        "Redemption details clear on voucher/email? If missing → BizOps via Retool/CO assistant",
        "AG in-app issues (playback/sharing/language vs booking instructions) → Tech bug alert; follow up on thread",
        "AG not received → check fulfillment + what was booked vs sent (BizOps if automated / RO if manual, then Tech)",
        "Any ticket/fulfillment/resolution issues?",
        "Tickets sent within timeframe (payment receipt)?",
        "Tickets correct (date, time, pax, experience, type)?",
        "Partial refund AG per DSS",
        "Similar issues in other reviews → flag to Arpit/relevant team",
    ],
    "SP — guided tour quality": [
        "Was it escalated before the review, with timely SP follow-up + resolution? Any RO/CE error?",
        "Cancellation/reschedule raised with SP when DOV not past-dated?",
        "On-tour / inclusions-not-met / guide issues raised with SP?",
        "Repeated pattern? Raise with Biz for recurring issues",
        "Impacted bookings/reviews for this TID-VID → raise with Biz if recurring",
        "SP given 48-72h to follow up; if uncontrollable, action per DSS",
    ],
    "SP — no shows / delays": [
        "Voucher redemption details correct — meeting point + map link match?",
        "Identifiers + redemption instructions present?",
        "Callouts/disclaimers present?",
        "Did guest follow redemption process (verify with proof)?",
        "Verify meeting point with SP if reported",
        "Major/recurring → Escalation team",
        "Check Slack threads for this TID-VID",
        "Impacted bookings/reviews for TID-VID → Business if recurring",
        "SP given 48-72h; if uncontrollable, action per DSS",
    ],
    "Unfulfilled booking": [
        "Reason for non-fulfilment — check Slack for automation failure or missed manual FF",
        "Escalate to IO/Tech if needed",
        "Tag RO/CE error for any FF issue",
        "Review completion rate for TID-VID/TGID over last 4 weeks",
        "Major/recurring → Escalation team",
        "Check Slack threads for TID-VID",
        "Impacted bookings/reviews → relevant team if recurring",
    ],
    "Untraceable booking": [
        "Use experience info + customer name to find booking on Looker/Slack/Zendesk",
        "Try different combinations to retrieve booking info",
    ],
    "Venue closure (weather/strike)": [
        "Check for closure news or proof (guest/internal/online)",
        "Review affected bookings; raise with Escalations",
        "Confirm partial vs full closure with SP + applicable refund amount",
        "Recurring issues for same TID/VID → add callouts",
        "If venue operational, request SP cancel/reschedule as exception",
    ],
    "Pricing / convenience fee": [
        "Verify variant + inclusions on site vs guest's claim of venue offer",
        "Cross-check final prices (incl. fees), variant, inclusions vs venue pricing",
        "If valid, partial refund of the difference to bank account",
    ],
    "Meeting point issues": [
        "Confirm meeting point with SP + cross-check other bookings",
        "Major/recurring → Escalations",
        "Check Slack threads for TID-VID",
        "Review impacted bookings/reviews for TID-VID",
    ],
    "Content issues": [
        "Check Slack threads for TID-VID",
        "Review impacted bookings/reviews for TID-VID",
        "Major/recurring → Escalations",
        "Raise with BizOps and BDM",
    ],
    "Guest error": [
        "Did guest reach out within policy window?",
        "Escalate to SP for cancel/reschedule as exception if needed",
        "Child ticket/pax-type concerns → relevant team",
        "Double booking → check time gap + DSS guidelines",
        "New tickets bought at venue + we fulfilled on time → request proof, refund once shared",
    ],
    "Refund issues": [
        "Refund-done tags on ZD updated + refund status on checkout?",
        "Refund done within promised timeframe?",
        "If not done, raise CE/RO error per the miss",
        "BMS refund error → raise with Leads on #co-issue or Fin on priority",
        "Share ARN number for delayed refunds",
    ],
    "Guest did not see tickets": [
        "Check Ticket_email_seen tag on ZD custom field",
        "Ticket delivery time aligns with ETA at purchase?",
        "ORM does NOT ask for proof of new tickets purchased",
        "Email guest with proof of when tickets were sent",
    ],
    "Invalid tickets": [
        "Tickets match booking: date, time, pax, experience, variant",
        "Raise with Inventory/Business/SP by FF type (prepurchase→IO, freesale→SP/Biz, Vendor API→check similar + SP/Biz)",
        "Other bookings same issue / ongoing internal issue?",
        "Major/recurring → Escalation team",
        "Can we send new tickets (future DOV) or refund?",
        "Only after all checks (2 weeks of bookings, correct ticket sent, no issue found) ask for docs to raise with SP (partnered only)",
    ],
    "Tickets sent late": [
        "Tickets sent within expected timeframe (payment receipt, experience page)?",
        "By FF type: Vendor API <10min→inv-ops-on-call else RO; Selenium not done in 30min + ticket issue→selenium-oncall else RO; Manual/PP/Free Sale→RO",
        "Other bookings same issue / ongoing internal issue?",
        "Major/recurring → Escalation team",
        "Resend tickets (future DOV) or refund/credits per DSS",
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# Scenario routing (Task #13)
# Routing precedence:  sub-theme family (by code)  →  L2 fallback  →  none.
# The FIRST scenario in each list is the PRIMARY; the rest are OVERLAYS.
# Scenario names must match SCENARIO_CHECKS keys (so actions/checks resolve).
# Two non-check sentinels are allowed: "general" and None (CE-error guideline).
# ─────────────────────────────────────────────────────────────────────────────

# (L2, sub_theme_code) -> [primary, *overlays].  "*" = applies to every code.
# The Supply-Partner framework is shared across all SP L2s, so it is keyed by
# the sentinel "__SP__" and matched on L1 == "Supply Partner Issue".
SCENARIO_ROUTING_SUBTHEME = {
    ("Meeting Point Issues", "*"):                                  ["Meeting point issues"],

    ("Ticket Issues", "A"): ["Invalid tickets", "Redemption issue with tickets"],
    ("Ticket Issues", "B"): ["Guest did not see tickets", "Tickets sent late"],
    ("Ticket Issues", "C"): ["Tickets sent late"],
    ("Ticket Issues", "D"): ["Invalid tickets", "Redemption issue with tickets"],
    ("Ticket Issues", "E"): ["Unfulfilled booking"],
    ("Ticket Issues", "F"): ["Content issues", "Redemption issue with tickets"],
    ("Ticket Issues", "G"): ["Redemption issue with tickets", "Invalid tickets"],

    ("Audio Guide Issues", "*"):                                    ["AG redemption issues"],

    ("__SP__", "A"): ["SP — no shows / delays"],
    ("__SP__", "B"): ["SP — guided tour quality"],
    ("__SP__", "C"): ["SP — guided tour quality"],
    ("__SP__", "D"): ["SP — no shows / delays"],
    ("__SP__", "E"): ["SP — guided tour quality"],
    ("__SP__", "F"): ["SP — no shows / delays", "Redemption issue with tickets"],
    ("__SP__", "G"): ["SP — guided tour quality"],

    ("Content - Instructions not clear / Misleading Info", "*"):    ["Content issues"],

    # Customer Support: only B (Refund) maps to a scenario; A/C/D/E/F/G → CE-error
    # guideline (no scenario → None), CE checks still always run.
    ("Customer Support Issues", "B"): ["Refund issues"],
}

# L2 -> [primary, *overlays] for families with NO sub-theme framework.
SCENARIO_ROUTING_L2 = {
    "Venue closure":                    ["Venue closure (weather/strike)"],
    "Customer expectation mismatch":    ["Content issues"],
    "Inventory Listing Issue":          ["Unfulfilled booking"],
    "App and Website Issues":           ["Tech error during booking"],
    "Seating Issues":                   ["SP — guided tour quality", "Redemption issue with tickets"],
    "Food & Catering":                  ["SP — guided tour quality", "Redemption issue with tickets"],
    "Venue facility issue":             ["Venue closure (weather/strike)"],
    "Venue Overcrowding (Venue)":       ["Venue closure (weather/strike)"],
    "Venue Overcrowding (External)":    ["Venue closure (weather/strike)"],
    "Pricing Issues":                   ["Pricing / convenience fee"],
    "Customer Late":                    ["Guest error"],
    "Customer Error":                   ["Guest error"],
    "Weather Related":                  ["Venue closure (weather/strike)"],
    "Force Majeure":                    ["Venue closure (weather/strike)"],
    "Sold Free / Discounted Admission": ["Pricing / convenience fee"],
    "Rating Mismatch":                  ["general"],
    "Gibberish / Profanity":            ["general"],
    "Vague review":                     ["general"],
    "Negative Headout":                 ["general"],
    "General negative exp":             ["general"],
}


def _sub_theme_code(sub_theme) -> str:
    """'A. Ticket Invalid / Not Working' -> 'A'.  Returns '' when absent."""
    if not sub_theme:
        return ""
    m = re.match(r"\s*([A-Z])[\.\)\s]", str(sub_theme).strip() + " ")
    return m.group(1) if m else ""


def scenarios_for(l1: str, l2: str, sub_theme=None) -> dict:
    """
    Resolve the scenario(s) for a classification.

    Returns {"primary": str|None, "overlays": [str], "all": [str]}.
    - "primary" is the header chip (may be a SCENARIO_CHECKS key, "general",
      or None for the CE-error-guideline case).
    - "overlays" stack under the primary in the What-Went-Wrong section.
    """
    l1 = (l1 or "").strip()
    l2 = (l2 or "").strip()
    code = _sub_theme_code(sub_theme)

    scen = None
    if l1 == "Supply Partner Issue" and ("__SP__", code) in SCENARIO_ROUTING_SUBTHEME:
        scen = SCENARIO_ROUTING_SUBTHEME[("__SP__", code)]
    elif (l2, code) in SCENARIO_ROUTING_SUBTHEME:
        scen = SCENARIO_ROUTING_SUBTHEME[(l2, code)]
    elif (l2, "*") in SCENARIO_ROUTING_SUBTHEME:
        scen = SCENARIO_ROUTING_SUBTHEME[(l2, "*")]

    if scen is None:
        scen = SCENARIO_ROUTING_L2.get(l2)

    scen = list(scen) if scen else []
    return {
        "primary":  scen[0] if scen else None,
        "overlays": scen[1:],
        "all":      scen,
    }


def compute_overlay_scenarios(l1: str, l2: str, sub_theme=None,
                              ticket_facts: dict = None,
                              booking: dict = None) -> list:
    """
    Overlay scenarios for the What-Went-Wrong stack. Starts from the static
    routing overlays and adds dynamic ones from ticket_facts / booking signals
    (never duplicating the primary). Safe on missing data.
    """
    routed = scenarios_for(l1, l2, sub_theme)
    primary = routed["primary"]
    overlays = list(routed["overlays"])
    tf = ticket_facts or {}
    bk = booking or {}

    def _add(name):
        if name and name != primary and name not in overlays and name in SCENARIO_CHECKS:
            overlays.append(name)

    # Refund signal present but not already a refund scenario → stack it.
    refund = tf.get("refund") or {}
    if isinstance(refund, dict) and (refund.get("issued") or refund.get("out_of_policy")):
        _add("Refund issues")
    # Cancelled booking with no fulfilment → unfulfilled overlay.
    status = str(bk.get("booking_status") or bk.get("status") or "").lower()
    if "cancel" in status:
        _add("Unfulfilled booking")
    return overlays


def scenario_actions(scenario_name: str) -> list:
    """Guideline action items for one scenario (from the baked Guidelines)."""
    return list(SCENARIO_CHECKS.get(scenario_name, []))


# Owner routing for Actions-Taken tabs. First matching rule wins; an item that
# matches no rule is a pure check (not an ownable action) and is skipped.
# First match wins, so the order IS the routing. SP is first for a reason.
#
# Refunds used to route to CUSTOMER and experience problems to BUSINESS. Both
# were wrong: a refund is nearly always a claim against the supply partner, and
# a redemption or quality problem IS the supply partner's. Filing them
# elsewhere sent the work to a team that could not action it, and the team who
# could never saw it.
#
# Business keeps what is genuinely commercial - inventory, pricing, the
# escalation ladder - and nothing else.
_OWNER_RULES = [
    # SP: the supply partner, anything claimed against them, and anything
    # about the EXPERIENCE itself - redemption, quality, the guide, the venue.
    ("sp",       ["supply partner", "raise with sp", "with sp", "→ sp", "sp/biz",
                  "operator", "guide", "vendor api", "vendor",
                  # refunds and money claimed back are claims against the SP
                  "refund", "credit note", "chargeback", "arn",
                  # the experience itself
                  "redemption", "redeem", "voucher", "qr", "entry denied",
                  "turned away", "quality", "poor experience", "meeting point",
                  "no-show", "overbooked", "venue"]),
    ("product",  ["tech team", "with tech", "raise with tech", "tech for", "tech bug",
                  "bms", "pdf", "app issue", "app issues", "selenium", "website", "→ tech"]),
    ("business", ["inventory", "inv-ops", "io/", "→ io", "prepurchase→io", " biz", "business",
                  "bdm", "bizops", "escalation team", "escalations", "arpit", "leads",
                  "#co-issue", " fin ", "on priority", "pricing", "commercial"]),
    ("customer", ["resend", "email guest", "reschedule",
                  "cancel/reschedule", "share proof", "request proof", "callout"]),
    ("ce",       ["ce error", "ce/ro", "macro", "clarity", "internal notes", "follow up",
                  "48-72h", "action per dss", "tag", "tagging", "not handled",
                  "mishandled", "process gap", "sop"]),
]


# A question is a check, not an action taken. The Guidelines sheet mixes the
# two, and the SP tab filled with "Refund-done tags on ZD updated + refund
# status on checkout?" and "Refund done within promised timeframe?" — the
# dashboard asking the reader whether the work was done, under a heading that
# claims to record what WAS done.
#
# Matched on shape, not on a word list: a check is phrased as a question, or
# as a bare "verify / confirm / check that" instruction. Anything else is an
# action someone can have taken.
_CHECK_RE = re.compile(
    r"\?\s*$|^\s*(?:verify|confirm|check|ensure|validate|did|is|are|was|were|has|have)\b",
    re.I)


def is_check(text: str) -> bool:
    """Whether this Guidelines row asks a question rather than naming an action.

    Actions Taken records what was done. A check belongs to the RCA's own
    reasoning, not to a list of completed work, and putting one there means
    the card cannot distinguish "we did this" from "someone should look at
    whether this was done".
    """
    return bool(_CHECK_RE.search(str(text or "").strip()))


def _owner_for_action(text: str):
    if is_check(text):
        return None
    t = " " + (text or "").lower() + " "
    for owner, kws in _OWNER_RULES:
        if any(k in t for k in kws):
            return owner
    return None


def actions_for(scenario_names) -> dict:
    """
    Merge + dedupe guideline actions across the given scenarios and route each
    to its owner tab. Returns {sp, customer, business, ce, product: [str]}.
    Only ownable action items are included (pure checks are skipped).
    """
    tabs = {"sp": [], "customer": [], "business": [], "product": [], "ce": []}
    seen = set()
    for name in (scenario_names or []):
        for item in scenario_actions(name):
            key = item.strip().lower()
            if key in seen:
                continue
            owner = _owner_for_action(item)
            if not owner:
                continue
            seen.add(key)
            tabs[owner].append(item)
    return tabs


# ─────────────────────────────────────────────────────────────────────────────
# Issue-specific questions — the EXACT questions the RCA answers, per scenario.
#
# These were previously left to the model ("draw from the issue-type
# diagnostics"), which produced a different, largely irrelevant set on every
# run - questions about how the team handled the case, questions the data
# cannot answer, and none of the ones a reviewer actually asks. A fixed bank
# per scenario makes the section comparable across RCAs and auditable.
#
# Rule for anything added here: it must be about the guest's EXPERIENCE and
# answerable from the experience page, booking, or ticket data. How CE/RO
# handled the contact belongs in flags and sop_compliance, never here.
# ─────────────────────────────────────────────────────────────────────────────

ISSUE_QUESTIONS = {
    "Tickets sent late": [
        "Was the ticket delivery window disclosed on the experience page before purchase?",
        "Were tickets delivered inside the window that was communicated?",
        "How long after booking were tickets actually delivered?",
        "Was the booking same-day or close-dated (less runway to absorb a delay)?",
        "Does the fulfilment type used match what the experience page promises?",
    ],
    "Guest did not see tickets": [
        "Was the ticket email delivered, and does the Ticket_email_seen tag show it was opened?",
        "Did delivery time match the ETA communicated at purchase?",
        "Were tickets sent to the email address on the booking?",
    ],
    "Invalid tickets": [
        "Do the tickets match the booking: date, time, pax, experience, variant?",
        "Was the ticket rejected at the venue, and on what stated ground?",
        "Which fulfilment type produced the ticket (prepurchase / freesale / vendor API)?",
        "Did other bookings on this TID-VID hit the same invalidity?",
    ],
    "Unfulfilled booking": [
        "Was the booking fulfilled at all, and if not, at which step did it stop?",
        "Was there an automation failure recorded for this booking?",
        "What is the completion rate for this TID-VID over the recent window?",
    ],
    "Redemption issue with tickets": [
        "Were the redemption instructions on the voucher complete and correct?",
        "Was entry denied, and on what stated ground?",
        "Is this a fulfilment, technical, or operational failure?",
    ],
    "Meeting point issues": [
        "What meeting point does the experience page show, and does the map link match it?",
        "Had the meeting point changed, and did we know before the guest travelled?",
        "Does the voucher meeting point match the variant name and the true meeting point?",
        "Were identifiers and redemption instructions present on the voucher?",
    ],
    "SP — no shows / delays": [
        "Was the guide or host present at the meeting point at the stated time?",
        "Were the voucher redemption details (meeting point, map link, identifiers) correct?",
        "Did the guest follow the redemption process as documented?",
        "Was a working SP contact on file for the guest to reach on the day?",
    ],
    "SP — guided tour quality": [
        "What did the experience page promise about the guide, language, and group size?",
        "Which promised inclusions were not delivered?",
        "Is this a recurring complaint for this TID-VID?",
    ],
    "Venue closure (weather/strike)": [
        "Was the venue closed on the date of visit, and is there proof?",
        "Was the closure partial or full?",
        "Did the experience page carry a closure or weather callout?",
        "Did we know about the closure before the guest travelled?",
    ],
    "Pricing / convenience fee": [
        "What did the guest pay in total, including all fees?",
        "What does the experience page list as included in the variant booked?",
        "Is the price difference the guest claims supported by the venue's own pricing?",
        "Was any convenience fee disclosed at checkout?",
    ],
    "Content issues": [
        "What does the experience page state about the specific point the guest disputes?",
        "Is the page content accurate as of the booking date?",
        "Is the discrepancy present for other bookings on this TID-VID?",
    ],
    "AG redemption issues": [
        "Were the audio-guide redemption details present on the voucher or email?",
        "Was the audio guide delivered, and in the language selected at booking?",
        "Does the variant booked actually include the audio guide?",
    ],
    "Refund issues": [
        "Was a refund due under the policy shown on the experience page?",
        "Was the refund issued, for how much, and when?",
        "Was it inside the timeframe promised to the guest?",
    ],
    "Guest error": [
        "What did the guest book versus what they say they intended to book?",
        "Did the guest contact us inside the policy window shown at checkout?",
        "Does the cancellation policy on the experience page cover this request?",
    ],
    "Tech error during booking": [
        "What did the guest attempt to book, and what did the booking record?",
        "Is there evidence of the failure in clarity or the booking flow?",
        "Did the checkout page display correct information at the time of booking?",
    ],
    "Untraceable booking": [
        "What identifying details did the guest give, and which sources were searched?",
        "Is there any booking matching the experience and name given?",
    ],
}

# Used when no scenario routes, or to top up a thin scenario set.
GENERAL_ISSUE_QUESTIONS = [
    "What did the experience page promise on the point the guest disputes?",
    "What does the booking record actually show?",
    "Is this a one-off or does it recur on this TID-VID?",
]


def issue_questions_for(scenario_names) -> list:
    """The exact experience-side questions this RCA must answer, deduped in
    routing order. Falls back to the general set when nothing routed."""
    out, seen = [], set()
    for name in (scenario_names or []):
        for q in ISSUE_QUESTIONS.get(name, []):
            k = q.lower()
            if k not in seen:
                seen.add(k)
                out.append(q)
    if not out:
        return list(GENERAL_ISSUE_QUESTIONS)
    return out
