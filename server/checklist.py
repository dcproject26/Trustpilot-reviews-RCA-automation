"""
Baked-in RCA checklist — verbatim from Brief v7.1.
No runtime fetch. No Google Sheets dependency.
"""
import re

from server.taxonomy import ACTION_TABS

# The nine teams, in the order the ORM team gave them. One vocabulary, shared
# by Actions Taken and Flags, because the two are joined on it.
ACTION_TEAMS = tuple(ACTION_TABS)

# UNROUTED IS A TAB, NOT A TEAM. It is deliberately absent from ACTION_TEAMS,
# which is the vocabulary flags and fix owners are validated against — a flag
# claiming team "unrouted" would be a team nobody can hand work to. It exists
# only as the first tab in Actions Taken, so a fix with no owner is somewhere
# a reader can see it rather than in a report line under a tab strip that
# looks complete.
UNROUTED = "unrouted"
ACTION_TAB_ORDER = (UNROUTED,) + ACTION_TEAMS

# What a flag written under an older vocabulary means in this one. CE and RO
# were the two support-side chips and both are the CO team's work now. Anything
# not listed here and not one of the nine reads as unrouted, which raises
# nothing — it does not silently become somebody's problem.
FLAG_TEAM_ALIASES = {
    "ce": "co", "ro": "co", "customer": "co", "cs": "co",
    "business": "biz", "bizops": "biz",
    "fin": "finance", "io": "inventory",
}

# Mandated structure for the "What went wrong" section (Headout ORM).
# Headings 1–5 are ALWAYS included; the (a)/(b)/(c) sub-points are indicative —
# use only the ones relevant to the case. Objective: concise, structured, and
# focused on the operational failure, NOT a restatement of the customer's review.
# FOUR HEADINGS, and the objective is stated because it is what the headings
# are for: make the RCA concise, structured, and focused on explaining the
# OPERATIONAL FAILURE — not on restating the customer's review.
#
# The headings are mandatory and always appear. The sub-points are INDICATIVE:
# use the ones this case has, drop the ones it does not.
#
# SUPPLY PARTNER ESCALATION USED TO BE HEADING 4 AND IS GONE FROM HERE. Not
# dropped — it already has a better home in `sp_interaction_notes`, which
# carries `raised` (Yes/No/N/A), the `reason` when it did not happen, and the
# records of what was raised and what came back. A mandatory WWR heading
# repeating that put "Did CE escalate to SP? Not recorded" under every issue
# on every card, including the many where no supply partner was involved at
# all — a heading answering a question nobody asked, five times per review.
WHAT_WENT_WRONG_OBJECTIVE = (
    "Concise, structured, and focused on explaining the operational failure "
    "rather than restating the customer's review."
)

WHAT_WENT_WRONG_STRUCTURE = [
    "1. Guest issue — brief 1–2 line summary (concise pointers) of the issue the guest experienced.",
    "2. Is the guest's claim accurate? — state one of: Yes / Partially True / No.",
    "3. What actually happened? — (a) root cause; (b) operational failure, if any; (c) SOP/process gap, if any.",
    "4. Fixes — (a) briefly mention any corrective actions taken or proposed.",
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
# First match wins, so the order IS the routing.
#
# The five old tabs (SP / Customer / Business / CE / Product) are gone. They
# were not teams: "Customer" is not one, "Business" was inventory, pricing and
# the escalation ladder in one chip, and a catalog problem or a refund Finance
# has to execute had nowhere to go at all. The nine below are the teams the ORM
# team actually raises things with, and they are the SAME vocabulary the flags
# use - Actions Taken and Flags are joined on it.
#
# ORDER, and why each rule sits where it does:
#   inventory before tech   - "Raise with Inventory/Business/SP by FF type
#                             (… Vendor API→check similar…)" names IO as the
#                             owner and Vendor API only as the trigger.
#   finance before co       - a refund Finance executes ("Fin on priority",
#                             an ARN, a bank transfer) is not the same work as
#                             a refund CO issues under DSS.
#   product before sp       - an app or checkout failure that happens to
#                             mention a voucher is the flow's, not the vendor's.
#   content before product  - HANDOFF §2: a missing or wrong VARIANT, PAX TYPE,
#                             INCLUSION or PAGE STATEMENT is Content/Catalog/
#                             Media, whoever else the row happens to mention.
#                             Product is for the flow, app or site failing to
#                             do its job with a CORRECT catalog.
_OWNER_RULES = [
    # The guest's own mistake, and nothing for a team to do. Named first
    # because it is the one verdict that must not be swallowed by a rule that
    # merely mentions the guest ("Email guest with proof" is CO's work).
    ("guest",     ["guest error", "customer error", "double booking",
                   "guest fault", "guest did not follow"]),
    # Inventory: fulfilment ownership by FF type, IO on-call, listing stock.
    ("inventory", ["inventory", "inv-ops", "io/", "→ io", "prepurchase→io",
                   "inv ops", "sold out"]),
    # Tech: Headout's own systems failing - BMS, PDFs, the automation.
    ("tech",      ["tech team", "with tech", "raise with tech", "tech for",
                   "tech bug", "→ tech", "bms/", "pdf", "selenium",
                   "vendor api issues", "automation failure"]),
    # Finance: money that has to move, and the records that prove it moved.
    ("finance",   [" fin ", "fin on priority", " arn ", "arn number",
                   "chargeback", "bank account", "refund error", "payment gateway"]),
    # Content/Catalog/Media: what the product SAYS. The catalog config, the
    # page, the voucher copy, the callouts.
    ("content",   ["content", "catalog", "media team", "callout", "disclaimer",
                   "inclusions", "pax type", "pax-type", "child ticket",
                   "experience page", "page statement", "redemption details",
                   "listing copy", "variant name"]),
    # Product: the guest-facing flow, app or site failing with a catalog that
    # is correct. A missing pax type is NOT this - that is Content.
    ("product",   ["booking flow", "app issue", "app issues", "in-app",
                   "website", "checkout flow", "web flow"]),
    # SP: the supply partner and anything claimed against them - the guide,
    # the venue, the meeting point, the tour itself.
    ("sp",        ["supply partner", "raise with sp", "with sp", "→ sp",
                   "sp/biz", "sp given", "escalate to sp", "request sp",
                   "operator", "guide", "vendor",
                   "redemption", "redeem", "voucher", "entry denied",
                   "turned away", "quality", "poor experience", "meeting point",
                   "no-show", "overbooked"]),
    # NOT a bare "venue". "New tickets bought at venue + we fulfilled on time →
    # request proof, refund once shared" is CO's work - we ask the guest for
    # the receipt and refund it. Matching it to SP on the word "venue" filed a
    # CO action with the supply partner, who has nothing to do.
    # Biz: the commercial relationship and the escalation ladder.
    ("biz",       ["bizops", " biz", "biz ", "business", "bdm", "escalation team",
                   "escalations", "arpit", "recurring", "pricing", "commercial"]),
    # CO: the support desk itself - how the contact was handled, what DSS
    # prescribed, and the remedies CO issues to the guest.
    ("co",        ["ce error", "ro error", "ce/ro", "macro", "clarity",
                   "internal notes", "follow up", "48-72h", "action per dss",
                   "#co-issue", "co assistant", "leads", "tagging", "tag ",
                   "not handled", "mishandled", "process gap", "sop",
                   "resend", "email guest", "reschedule", "cancel/reschedule",
                   "share proof", "request proof", "refund", "credits"]),
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
_QUESTION_RE = re.compile(r"\?\s*$")
_CHECK_VERB_RE = re.compile(
    r"^\s*(?:verify|confirm|check|ensure|validate|did|is|are|was|were|has|have)\b",
    re.I)
# A leading "check" is only a check when nothing says who to do it WITH.
# "Check inventory with IO" is an action - it names the team - and reading it
# as a check deleted a real Business row from the tab, which is worse than the
# bug being fixed.
_ROUTING_RE = re.compile(
    r"\bwith\b|\braise\b|\bshare\b|\bemail\b|\bescalate\b|→|->|#\w", re.I)


def is_check(text: str) -> bool:
    """Whether this Guidelines row asks a question rather than naming an action.

    Actions Taken records what was done. A check belongs to the RCA's own
    reasoning, not to a list of completed work, and putting one there means
    the card cannot distinguish "we did this" from "someone should look at
    whether this was done".
    """
    t = str(text or "").strip()
    if not t:
        return False
    if _QUESTION_RE.search(t):
        return True
    return bool(_CHECK_VERB_RE.search(t)) and not _ROUTING_RE.search(t)


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
    to its owner tab. Returns {team: [str]} over ACTION_TEAMS.
    Only ownable action items are included (pure checks are skipped).

    This is HALF of what Actions Taken shows: what the DSS guidelines say must
    be raised for the routed scenarios. The other half is whether anything was
    actually flagged - see `actions_raised`, which is what the card renders.
    """
    tabs = {t: [] for t in ACTION_TEAMS}
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


def team_of_flag(flag) -> str:
    """The ACTION_TEAMS key a flag names, or "" when it names none.

    Flags carry the team CODE (the tab key upper-cased). Drafts written before
    the nine-team vocabulary carry CE, RO, CUSTOMER, BUSINESS or OTHER, and a
    flag whose team cannot be read must not quietly become some team's problem:
    it returns "" and raises nothing.
    """
    code = str((flag or {}).get("team") or "").strip().lower() \
        if isinstance(flag, dict) else str(flag or "").strip().lower()
    code = FLAG_TEAM_ALIASES.get(code, code)
    return code if code in ACTION_TEAMS else ""


# Every action string the guidelines can produce, anywhere in the corpus.
# Used to tell a row a PERSON typed from one the sheet supplied: presence here
# means the sheet wrote it, whatever this particular card routed.
_ALL_GUIDELINE_ACTIONS = {
    str(a).strip()
    for rows in SCENARIO_CHECKS.values()
    for a in rows
    if str(a).strip()
}


# Words that carry no subject matter. A guideline row and a finding sharing
# only "with", "the" or "raise" have nothing to do with each other.
_RELEVANCE_STOP = {
    "the", "a", "an", "of", "to", "for", "and", "or", "in", "on", "at", "by",
    "is", "was", "were", "be", "been", "with", "from", "this", "that", "it",
    "if", "as", "not", "no", "any", "all", "has", "have", "had", "will",
    "raise", "raised", "share", "check", "verify", "confirm", "ensure",
    "team", "teams", "issue", "issues", "case", "guest", "customer", "please",
    "via", "per", "our", "their", "his", "her", "them", "they", "we", "us",
    "when", "where", "which", "who", "what", "how", "then", "than", "so",
}


def _relevance_tokens(text) -> set:
    """Subject-matter words in a string, lower-cased and stemmed crudely.

    A trailing 's' is dropped so "refunds" matches "refund" and
    "reassignments" matches "reassignment". Crude on purpose: the cost of a
    loose match is a row that stays on the card, which is the safe direction.
    """
    out = set()
    for t in re.findall(r"[a-z]{3,}", str(text or "").lower()):
        if t in _RELEVANCE_STOP:
            continue
        out.add(t[:-1] if len(t) > 4 and t.endswith("s") else t)
    return out


def findings_text(rca) -> str:
    """Everything on a card a guideline row could bear on.

    The root cause, the operational failure, the SOP gap and the flags — the
    four things the card actually FOUND. Built here rather than at the call
    site so the pipeline and regenerate-rca cannot assemble it differently.
    """
    rca = rca if isinstance(rca, dict) else {}
    bits = []
    wwr = rca.get("what_went_wrong")
    for gi in ((wwr or {}).get("guest_issues") or []):
        if not isinstance(gi, dict):
            continue
        for k in ("issue", "root_cause", "operational_failure", "sop_gap"):
            if gi.get(k):
                bits.append(str(gi[k]))
        fx = gi.get("fix")
        if isinstance(fx, dict):
            for k in ("action", "because"):
                if fx.get(k):
                    bits.append(str(fx[k]))
    for f in (rca.get("flags") or []):
        if isinstance(f, dict):
            for k in ("flag", "evidence"):
                if f.get(k):
                    bits.append(str(f[k]))
    return " ".join(bits)


def actions_raised(scenario_names, flags, keep=None,
                   findings=None) -> tuple[dict, dict]:
    """Actions Taken, and what it could not raise.

    THE RULE IS AN AND OF THREE THINGS. A row appears because the DSS
    guidelines say it must be raised for the routed scenario, AND because a
    flag on this card names the team it belongs to, AND because it BEARS ON
    WHAT THIS CASE FOUND. Guidelines alone would put the same generic list on
    every card of a given L2, flagged alone would invent work nobody's playbook
    asks for, and the card is read as "this is what we did", so no half may
    carry it on its own.

    THE THIRD CONDITION, and why two were not enough. A booking reassigned to
    a new operator without the guest's consent, remedied with a partial wallet
    credit, showed three rows on the Supply Partner tab: "Verify meeting point
    with SP if reported", "BMS refund error -> raise with Leads", and "Share
    ARN number for delayed refunds". No meeting point, no BMS refund error, no
    delayed refund. All three satisfied the AND — routed scenario, flag naming
    SP — and all three were still wrong. A guideline row for a scenario is not
    automatically a step that was or should have been taken on THIS booking.

    Relevance is decided by subject-matter overlap with the card's own
    findings: the root cause, the operational failure, the SOP gap, the fix and
    the flags. Crude on purpose — a loose match leaves a row on the card, which
    is the safe direction, while a tight one silently empties a tab.

    `findings` IS OPTIONAL AND ITS ABSENCE IS REPORTED. A caller that does not
    pass it gets the two-condition behaviour, and the report says the filter
    did not run — because a filter that never ran and a filter that ran and
    withheld nothing are the thing this codebase must never confuse.

    Returns (tabs, report). The report is not decoration: a tab that is empty
    because nothing was flagged, a tab empty because the guidelines list
    nothing, and a tab empty because nothing the guidelines list bears on this
    case are the same blank space on screen and mean three different things.
    `report["notes"]` is written for the confidence trail.

    """
    guideline = actions_for(scenario_names)
    flags = [f for f in (flags or []) if isinstance(f, dict)]
    flagged = {t for t in (team_of_flag(f) for f in flags) if t}

    # The third condition. `findings is None` means the caller did not ask for
    # it; `""` means it asked and the card had nothing to say, which is a
    # different fact and is reported differently below.
    _rel_ran = findings is not None
    _find_toks = _relevance_tokens(findings) if _rel_ran else set()
    _irrelevant = {}

    def _bears_on_the_case(team, row):
        if not _rel_ran:
            return True
        if not _find_toks:
            # Nothing was found on this card, so nothing can be shown to bear
            # on it. Withholding everything here would be a filter deciding a
            # question it has no evidence for, so it stands aside and says so.
            return True
        row_toks = _relevance_tokens(row)
        if not row_toks:
            return True
        if row_toks & _find_toks:
            return True
        # A row also earns its place from the flag that routed its team: the
        # flag IS a finding, and its wording is often closer to the guideline
        # row than the prose of the root cause.
        for f in flags:
            if team_of_flag(f) != team:
                continue
            if row_toks & _relevance_tokens(
                    f"{f.get('flag', '')} {f.get('evidence', '')}"):
                return True
        return False

    tabs = {}
    for t, items in guideline.items():
        if t not in flagged:
            tabs[t] = []
            continue
        keep_rows, drop_rows = [], []
        for row in items:
            (keep_rows if _bears_on_the_case(t, row) else drop_rows).append(row)
        tabs[t] = keep_rows
        if drop_rows:
            _irrelevant[t] = drop_rows

    # A ROW SOMEONE TYPED SURVIVES A RE-RUN. This is recomputed from the
    # guidelines and the flags every time, so a row an associate added by hand
    # was silently gone the next time the RCA was regenerated — the work of
    # deciding it mattered, discarded with nothing on screen to say so.
    #
    # `keep` is the PREVIOUS tabs. Anything in it that the guidelines do not
    # contain was put there by a person, so it is carried forward. Anything
    # the guidelines DO contain is left to the rule above, or a withheld row
    # would return by the back door and the AND would stop meaning anything.
    _kept = 0
    for _t, _items in (keep or {}).items():
        if _t not in tabs:
            continue
        # Compared against the WHOLE guideline corpus, not just the rows
        # routed for this card. Against the routed set alone, a row the AND
        # deliberately withheld — or one whose wording changed in the sheet —
        # reads as hand-typed and comes back through this door, which is the
        # AND quietly stopping to mean anything.
        _known = _ALL_GUIDELINE_ACTIONS
        for _row in (_items or []):
            _txt = str(_row or "").strip()
            if _txt and _txt not in _known and _txt not in tabs[_t]:
                tabs[_t].append(_txt)
                _kept += 1
    withheld = {t: items for t, items in guideline.items()
                if items and t not in flagged}
    n_withheld = sum(len(v) for v in withheld.values())

    notes = []
    if _kept:
        # Said out loud: these rows are on the card for a different reason
        # from the rest, and a reader comparing two runs deserves to know
        # which survived because a person wrote them.
        notes.append(f"actions taken: {_kept} hand-added row(s) carried "
                     f"forward through this re-run")
    if not scenario_names:
        notes.append("actions taken: no scenario routed, so the guidelines "
                     "name nothing to raise")
    elif not any(guideline.values()):
        notes.append("actions taken: the routed scenario(s) have no guideline "
                     "action to raise — only checks")
    elif not flagged:
        notes.append(f"actions taken: {n_withheld} guideline action(s) withheld "
                     f"— nothing was flagged, so nothing is raised")
    elif n_withheld:
        notes.append(
            f"actions taken: {n_withheld} guideline action(s) withheld — no flag "
            f"names " + ", ".join(ACTION_TABS[t]["label"] for t in sorted(withheld))
            + "; flagged: " + ", ".join(ACTION_TABS[t]["label"] for t in sorted(flagged)))
    # The relevance filter announces itself. Grouping a guideline row with a
    # case by word overlap is a JUDGEMENT, and nothing else on the card says
    # one was made.
    n_irrelevant = sum(len(v) for v in _irrelevant.values())
    if not _rel_ran:
        notes.append("actions taken: relevance to this case was NOT checked — "
                     "rows are here because the guidelines list them and a flag "
                     "names the team, nothing more")
    elif n_irrelevant:
        notes.append(
            f"actions taken: {n_irrelevant} guideline action(s) withheld as not "
            f"bearing on this case (judged by subject-matter overlap with the "
            f"root cause, operational failure, SOP gap, fix and flags) — "
            + "; ".join(f"{ACTION_TABS[t]['label']}: "
                        + ", ".join(repr(r[:60]) for r in rows)
                        for t, rows in sorted(_irrelevant.items())))

    # Nothing withheld and something raised is the quiet case: the card shows
    # the rows, which is the report.
    return tabs, {
        "raised":        sum(len(v) for v in tabs.values()),
        "kept_by_hand":  _kept,
        "withheld":      n_withheld,
        "withheld_teams": sorted(withheld),
        "flagged_teams": sorted(flagged),
        "relevance_checked": _rel_ran,
        "irrelevant":    n_irrelevant,
        "irrelevant_teams": sorted(_irrelevant),
        "notes":         notes,
    }


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


# ─────────────────────────────────────────────────────────────────────────────
# Actions Taken, built from THIS CASE'S FINDINGS
# ─────────────────────────────────────────────────────────────────────────────
#
# `actions_raised` above sources its rows from the DSS guideline sheet for the
# routed scenario, then filters by flagged team and word overlap. Two filters
# on a wrong source: the rows are a PLAYBOOK for a scenario, not things that
# happened on this booking. That is how "Share ARN number for delayed refunds"
# and "BMS refund error -> raise with Leads on #co-issue or Fin on priority"
# reached a card with no delayed refund and no BMS error — both were valid
# guideline rows for the scenario, both satisfied the AND, and both were still
# statements about work nobody did.
#
# The section is read as "this is what we did / what must be done". So a row
# earns its place by BEING a finding on this card, not by resembling one:
#
#   1. a flag raised here
#   2. an operational failure found here
#   3. an SOP / process gap found here
#   4. a fix, with the team that owns it
#   5. an area-of-improvement point (already provenance-checked upstream —
#      _improvements drops any point whose stated source matches nothing)
#   6. the DSS MISS: what DSS says the next escalation step was, where it did
#      not happen
#
# DSS IS USED ONLY FOR 6. It works out what should have come next in the
# escalation; it is not an anchor, a definition, or a comment pasted onto the
# output, and it no longer supplies rows of its own.
#
# NOTHING IS INVENTED. Every row is a string that already exists on the card.

# Rows are deduplicated on subject-matter words, not on exact text: the same
# finding is written three ways across a root cause, a flag and an improvement
# point, and printing all three reads as three pieces of work.
_DEDUP_MIN_TOKENS = 3
_DEDUP_OVERLAP = 0.7
# ACTIONS ARE LOOSER THAN FINDINGS, and they have to be.
#
# A finding is a fact and two facts sharing half their words are usually two
# facts. An action is a PRESCRIPTION — "Require an agent to…", "Notify the
# guest whenever…", "Define a checklist requiring…" — and the same instruction
# written twice shares its subject and its object and almost nothing else:
#
#   "Require an agent to contact the guest proactively whenever a
#    system-initiated reschedule changes the start time"
#   "Require a proactive notification to the guest whenever a vendor
#    reassignment changes the confirmed start time"
#   "Notify the guest proactively whenever a reschedule results in a vendor or
#    time change that differs from what was confirmed"
#
# One instruction, three times, ~0.5 containment. At 0.7 all three printed,
# and a CO tab carried 32 rows that were perhaps eight pieces of work.
#
# 0.5 is MEASURED, not picked. On the reported CO tab the three restatements
# above sit at exactly 0.50 containment with each other, and the genuinely
# different action beside them —
#
#   "Require RO to verify the new operator's confirmed pickup time against the
#    rescheduled slot before sending any confirmation"
#
# — sits at 0.20-0.30 against all three. There is real daylight between the
# two groups, so the threshold falls in a gap rather than through a cluster.
#
# Every merge is still counted on the notes: a dial set too tight shows up as
# a tab full of restatements, one set too loose as a count that does not match
# the work. Findings keep 0.7 — nothing about them changed, and a fact sharing
# half its words with another fact is usually a second fact.
_DEDUP_OVERLAP_ACTION = 0.5

# A PROBLEM AND ITS REMEDY ARE NOT THE SAME ROW. "No alert exists for a
# stalled fulfilment run" and "Add an alert on a stalled fulfilment run" share
# almost every content word, and merging them drops the remedy — which is the
# half the reader is meant to act on. So rows only merge within their own
# half: findings with findings, actions with actions.
_ACTION_KINDS = {"fix", "dss_miss", "improvement"}


def _dedup_key(text) -> frozenset:
    return frozenset(_relevance_tokens(text))


def _group_of(kind) -> str:
    return "action" if kind in _ACTION_KINDS else "finding"


def _is_repeat(text, seen, group="finding") -> bool:
    """Whether this row says what an earlier one already said.

    Short rows are compared exactly — with one or two content words there is
    not enough to judge overlap on, and collapsing them would drop distinct
    findings that happen to share a word.
    """
    toks = _dedup_key(text)
    norm = " ".join(str(text or "").lower().split())
    if norm in seen["exact"]:
        return True
    if len(toks) < _DEDUP_MIN_TOKENS:
        return False
    for prev in seen["tokens"].get(group, []):
        # BOTH SIDES need enough words. The guard above checks the incoming
        # row and not the one it is compared against, so a two-token row
        # already on the list made ANY single shared word a 0.5 containment:
        # "Resend the tickets to the guest" {resend, ticket} swallowed "Refund
        # the second ticket" {refund, second, ticket} on the word "ticket".
        #
        # Containment over a two-word set is not a measurement of anything.
        if not prev or len(prev) < _DEDUP_MIN_TOKENS:
            continue
        overlap = len(toks & prev) / max(1, min(len(toks), len(prev)))
        if overlap >= (_DEDUP_OVERLAP_ACTION if group == "action"
                       else _DEDUP_OVERLAP):
            return True
    return False


def _remember(text, seen, group="finding") -> None:
    seen["exact"].add(" ".join(str(text or "").lower().split()))
    seen["tokens"].setdefault(group, []).append(_dedup_key(text))


def team_for_fix(issue, flags) -> tuple:
    """(team, how) — who owns this issue's fix, and on what basis.

    THE MIS-ROUTE THIS REPLACES. When `fix.owner` was absent the router fell
    back on `_sole`: the single team that happened to hold a flag anywhere on
    the card. One CONTENT flag and a refund fix sent the refund to CONTENT.
    That is the silent mis-route this module's own docstring calls worse than
    a row reported as unplaced.

    So the fallback is now a TIE TO THIS ISSUE rather than to the card: a flag
    whose wording overlaps this issue's own failure, gap or title. If nothing
    ties, the answer is "" and the caller reports it unrouted — the honest
    outcome, and distinguishable from a routed one because `how` says which
    of the three happened.
    """
    fix = issue.get("fix") if isinstance(issue.get("fix"), dict) else {}
    owner = str(fix.get("owner") or "").strip().lower()
    owner = FLAG_TEAM_ALIASES.get(owner, owner)
    if owner in ACTION_TEAMS:
        return owner, "the fix names its owner"

    mine = " ".join(str(issue.get(k) or "") for k in
                    ("issue", "operational_failure", "sop_gap"))
    if mine.strip():
        for f in (flags or []):
            if not isinstance(f, dict):
                continue
            text = f"{f.get('flag') or ''} {f.get('evidence') or ''}"
            if text.strip() and _overlaps_tokens(text, mine):
                t = team_of_flag(f)
                if t:
                    return t, "matched to a flag raised on this same issue"
    return "", "the fix names no owner and no flag on this issue matches it"


def _team_of_improvement(point, issues, flags) -> str:
    """The team an improvement point belongs to, from the finding it cites.

    `_improvements` has already checked that the point's stated source matches
    an operational failure, an SOP gap or a flag on this card, so the same
    match is re-run here to find WHICH one — and that finding's team is the
    point's team. A flag names its team directly; a failure or a gap is owned
    by whoever owns its issue's fix.

    Returns "" when the citation cannot be tied back, which routes the point
    to the unrouted report rather than to a guessed tab.
    """
    src = str(point.get("source") or "")
    kind = str(point.get("from") or "")
    if not src:
        return ""
    if kind == "flag":
        for f in flags or []:
            text = f"{f.get('flag') or ''} {f.get('evidence') or ''}"
            if _overlaps_tokens(src, text):
                return team_of_flag(f)
        return ""
    for i in issues or []:
        cand = i.get(kind) if kind in ("operational_failure", "sop_gap") else None
        if cand and _overlaps_tokens(src, cand):
            fix = i.get("fix") if isinstance(i.get("fix"), dict) else {}
            owner = str(fix.get("owner") or "").strip().lower()
            return FLAG_TEAM_ALIASES.get(owner, owner)
    return ""


def _overlaps_tokens(a, b) -> bool:
    """Half of the shorter side's content words appear in the other.

    The same threshold `_improvements` used to accept the citation, so a point
    it let through cannot fail to route here for a reason it was never told
    about.
    """
    ta, tb = _relevance_tokens(a), _relevance_tokens(b)
    if not ta or not tb:
        return False
    return len(ta & tb) / max(1, min(len(ta), len(tb))) >= 0.5


def actions_from_findings(issues, flags, improvements=None, dss_miss=None,
                          keep=None) -> tuple[dict, dict]:
    """Actions Taken from what this case found. Returns (tabs, report).

    `dss_miss` is [{team, action}] — the escalation step DSS prescribes that
    did not happen. It is the ONLY thing DSS contributes.

    A finding whose team cannot be determined is NOT parked on a plausible
    tab. It is counted and named in the report, because a row silently routed
    to the wrong team is worse than a row a reader is told went nowhere.
    """
    tabs = {t: [] for t in ACTION_TEAMS}
    seen = {"exact": set(), "tokens": {}}
    unrouted, counts = [], {}
    routed_how = {}

    def _add(team, text, kind):
        text = str(text or "").strip()
        if not text or team not in tabs:
            return False
        grp = _group_of(kind)
        if _is_repeat(text, seen, grp):
            counts["repeat"] = counts.get("repeat", 0) + 1
            return False
        _remember(text, seen, grp)
        tabs[team].append(text)
        counts[kind] = counts.get(kind, 0) + 1
        return True

    flags = [f for f in (flags or []) if isinstance(f, dict)]
    issues = [i for i in (issues or []) if isinstance(i, dict)]

    # The team a finding belongs to, when the finding itself does not say.
    # ONE flagged team is an unambiguous answer; several is a choice this
    # function has no basis to make, so it declines and says so.
    flagged_teams = [t for t in {team_of_flag(f) for f in flags} if t]
    _sole = flagged_teams[0] if len(flagged_teams) == 1 else ""

    def _route(explicit, text, kind, allow_sole=True):
        # NOTHING TO ROUTE IS NOT A ROUTING FAILURE. An issue with no
        # operational failure, no SOP gap and no fix is the ordinary shape of
        # a clean issue; counting its three empty fields as unplaceable
        # findings put "3 finding(s) name no team" on the trail of a run that
        # found nothing wrong. `_add` already ignored the empty text — only
        # the unrouted tally was reading absence as failure.
        if not str(text or "").strip():
            return False
        team = str(explicit or "").strip().lower()
        team = FLAG_TEAM_ALIASES.get(team, team)
        if team in tabs:
            return _add(team, text, kind)
        # `_sole` IS NOT OFFERED TO ISSUE-DERIVED ROWS. `team_for_fix` has
        # already tried the owner and a flag tied to that same issue; falling
        # through to "the one team with a flag somewhere on this card" would
        # undo that work and reinstate the mis-route it exists to stop.
        if _sole and allow_sole:
            return _add(_sole, text, kind)
        unrouted.append((kind, str(text or "")[:80]))
        return False

    # 1. THE FLAGS. Each one is a thing raised on this case, and the flag's own
    #    team is not a guess.
    for f in flags:
        _route(f.get("team"), f.get("flag"), "flag")

    # 2-4. What each issue found, and who owns the fix. The fix's owner routes
    #      the operational failure and the SOP gap too: they are the same
    #      finding seen from three angles, and the fix is the only one of the
    #      three that names a team.
    for i in issues:
        fix = i.get("fix") if isinstance(i.get("fix"), dict) else {}
        # Per-issue, not card-wide. `_sole` — the one team holding a flag
        # anywhere on the card — used to answer this, which put a refund fix
        # on CONTENT because CONTENT held the only flag.
        owner, how = team_for_fix(i, flags)
        if owner:
            routed_how[how] = routed_how.get(how, 0) + 1
        _route(owner, i.get("operational_failure"), "operational_failure",
               allow_sole=False)
        _route(owner, i.get("sop_gap"), "sop_gap", allow_sole=False)
        _route(owner, fix.get("action"), "fix", allow_sole=False)

    # 5. Area of improvement. Already provenance-checked upstream, so a point
    #    that survives to here has a source that exists on this card — and
    #    that source is what routes it. An AOI row carries {point, from,
    #    source} and no team of its own, so reading `p["team"]` would send
    #    every point to the unrouted pile: the section would report six
    #    findings it could not place on a card where all six were placeable.
    for p in (improvements or []):
        if not isinstance(p, dict):
            continue
        _route(_team_of_improvement(p, issues, flags),
               p.get("point") or p.get("text"), "improvement")

    # 6. THE DSS MISS, and the only thing DSS puts here.
    for m in (dss_miss or []):
        if isinstance(m, dict):
            _route(m.get("team"), m.get("action"), "dss_miss")

    # A row someone typed survives a re-run, exactly as before. Nothing here is
    # sourced from the guideline sheet any more, so a hand-typed row is simply
    # any previous row this rebuild did not produce.
    kept = 0
    for t, items in (keep or {}).items():
        if t not in tabs:
            continue
        for row in (items or []):
            txt = str(row or "").strip()
            if (txt and txt not in _ALL_GUIDELINE_ACTIONS
                    and not _is_repeat(txt, seen, "action")
                    and not _is_repeat(txt, seen, "finding")):
                _remember(txt, seen, "action")
                tabs[t].append(txt)
                kept += 1

    notes = []
    if kept:
        notes.append(f"actions taken: {kept} hand-added row(s) carried forward "
                     f"through this re-run")
    _matched = routed_how.get("matched to a flag raised on this same issue", 0)
    if _matched:
        notes.append(f"actions taken: {_matched} fix(es) named no owner and "
                     f"were routed by matching a flag raised on the same "
                     f"issue — a judgement, not a stated owner")
    if counts.get("repeat"):
        # A judgement, announced. Two findings worded differently were treated
        # as one, and nothing else on the card would say so.
        notes.append(f"actions taken: {counts['repeat']} row(s) said what "
                     f"another row already said and were merged")
    if unrouted:
        # The §1 line. These findings exist and are NOT on the card; a reader
        # must not have to infer that from a tab that looks complete.
        notes.append(
            f"actions taken: {len(unrouted)} finding(s) name no team and could "
            f"not be routed — " +
            "; ".join(f"{k}: {t}" for k, t in unrouted[:3]) +
            ("" if len(unrouted) <= 3 else f" (+{len(unrouted) - 3} more)"))
    if not any(tabs.values()):
        if not flags and not issues:
            notes.append("actions taken: nothing was flagged and no issue "
                         "recorded a failure, gap or fix — there is nothing "
                         "this case found to raise")
        else:
            notes.append("actions taken: this case has findings but none of "
                         "them named a team, so no row could be placed")

    report = {"counts": counts, "unrouted": unrouted, "kept": kept,
              "notes": notes}
    return tabs, report


def hand_typed_actions(stored, prev_gaps) -> tuple:
    """The Actions Taken rows a PERSON put there, and the ones we cannot tell.

    THE DEFECT THIS CLOSES. Both re-run paths passed the WHOLE previous
    `actions_taken` column as `keep`. `keep` exists so a row an associate
    typed survives a regenerate — but the column is mostly model output, so
    every row the old fixes-derived section ever produced was carried forward
    forever, immune to the rebuild.

    On a real card that showed as four rows on the CO tab, each of them a
    recommendation — "Require RO to verify the slot time…", "Require proactive
    outreach…" — which is precisely what the gaps rule says a gap is NOT. No
    current gap produced them. Nothing said they were stale. The tab looked
    like a section that had run.

    THE ATTRIBUTION. Rebuild the previous gaps and subtract: what the previous
    gaps explain is model output and is dropped, and the remainder is a
    person's. That is only possible where the previous gaps were STORED —
    before they were, there is no way to tell the two apart, and this returns
    the leftovers as UNATTRIBUTABLE rather than silently calling them
    hand-typed. The caller says so; a row carried forward on a guess must not
    read like a row someone owns.
    """
    stored = stored if isinstance(stored, dict) else {}
    known = set()
    if isinstance(prev_gaps, list):
        prior, _ = actions_from_gaps(prev_gaps, keep=None)
        for rows in prior.values():
            known.update(str(r).strip() for r in (rows or []))
    keep, unattributable = {}, 0
    for tab, rows in stored.items():
        for row in (rows or []):
            txt = str(row or "").strip()
            if not txt or txt in known:
                continue
            keep.setdefault(tab, []).append(txt)
            # NO STORED GAPS MEANS NO ATTRIBUTION, not "everything is
            # hand-typed". The row is still carried — deleting a person's work
            # on a guess is the expensive direction — but it is COUNTED, so a
            # tab full of unattributable rows is distinguishable from a tab
            # somebody filled in.
            if not isinstance(prev_gaps, list):
                unattributable += 1
    return keep, unattributable


def actions_from_gaps(gaps, keep=None) -> tuple:
    """Actions Taken as UNSOLVED GAPS, each raised with the team that owns it.

    The section was built from §3's fixes, which is why it carried findings,
    SOP gaps and recommendations in one undifferentiated list — 32 rows on a
    CO tab, among them "No one at Headout was aware of the vendor's time
    change" (a finding) and "No requirement exists to notify the guest" (a
    gap), sitting beside three wordings of one instruction.

    A row here is now one thing: something that is STILL WRONG and needs
    raising. "Chat miss — raise with CO". Present tense, because a gap that
    has been closed is not an action anybody has to take.

    NOTHING IS RAISED THAT THE DATA DOES NOT SHOW. Every gap must cite the
    ticket, contact or finding it was read from; one that cites nothing is a
    plausible process improvement rather than something this case surfaced,
    and it is dropped and counted. That is the whole guard against filling
    this tab with things a model thinks are generally true.

    Hand-typed rows are carried through a re-run exactly as before: a row
    somebody wrote is not model output and a rebuild must not eat it.
    """
    tabs = {t: [] for t in ACTION_TAB_ORDER}
    seen = {"exact": set(), "tokens": {}}
    counts = {"gap": 0, "repeat": 0, "unsourced": 0, UNROUTED: 0}

    for g in (gaps or []):
        if not isinstance(g, dict):
            continue
        text = str(g.get("gap") or "").strip()
        if not text:
            continue
        # THE ANTI-HALLUCINATION GATE. A gap with no source is not something
        # this case showed; it is something the model believes about cases in
        # general. Counted, so a run that raises nothing because nothing was
        # sourced is distinguishable from a case with no gaps.
        if not str(g.get("source_ref") or "").strip():
            counts["unsourced"] += 1
            continue
        if _is_repeat(text, seen, "action"):
            counts["repeat"] += 1
            continue
        _remember(text, seen, "action")
        team = str(g.get("team") or "").strip().lower()
        team = FLAG_TEAM_ALIASES.get(team, team)
        tab = team if team in ACTION_TEAMS else UNROUTED
        tabs[tab].append(text)
        counts["gap"] += 1
        if tab == UNROUTED:
            counts[UNROUTED] += 1

    kept = 0
    for t, items in (keep or {}).items():
        if t not in tabs:
            continue
        for row in (items or []):
            txt = str(row or "").strip()
            if txt and not _is_repeat(txt, seen, "action"):
                _remember(txt, seen, "action")
                tabs[t].append(txt)
                kept += 1

    notes = []
    if kept:
        notes.append(f"actions taken: {kept} hand-added row(s) carried forward "
                     f"through this re-run")
    if counts["unsourced"]:
        notes.append(f"actions taken: {counts['unsourced']} gap(s) cited no "
                     f"ticket, contact or finding and were NOT raised — a gap "
                     f"this case did not show is not this case's gap")
    if counts["repeat"]:
        notes.append(f"actions taken: {counts['repeat']} gap(s) said what "
                     f"another gap already said and were merged")
    if counts[UNROUTED]:
        notes.append(f"actions taken: {counts[UNROUTED]} gap(s) name no team "
                     f"and sit on the Unrouted tab — nobody picks those up "
                     f"until someone assigns them")
    if not counts["gap"] and not kept:
        notes.append("actions taken: no unsolved gap was found in this case, "
                     "so there is nothing for any team to pick up")

    return tabs, {"counts": counts, "kept": kept, "notes": notes,
                  "unrouted": counts[UNROUTED]}


def actions_from_fixes(fixes, keep=None) -> tuple:
    """Actions Taken as a VIEW over §3's fixes. Returns (tabs, report).

    TWO SOURCES: the fixes, and the rows a person typed. Not six — operational failures,
    SOP gaps, improvement points and the DSS missed step are each already a
    finding in their own section, and routing them here as well is how one
    finding reached a card three ways.

    A FLAG IS ACTIONABLE. It names the team that must act and what they are
    handed, which is exactly what a tab is for; dropping it left a real
    hand-off with nowhere to be picked up. Where a flag and a fix say the same
    thing, `_is_repeat` merges them and the run says it did — that is what
    stops the duplication, not excluding one of them.

    FIXES ARE ADDED FIRST, deliberately. A fix states what will be done and
    names its owner; a flag states what went wrong. When the two collide the
    fix is the row worth keeping, so it claims the slot and the flag merges
    into it rather than the other way round.

    AN UNOWNED FIX IS ON THE UNROUTED TAB, not in a footnote. The previous
    behaviour reported it in `notes` and left the tab strip looking complete,
    which is the shape of a finished card with a row nobody will pick up.

    `keep` carries hand-typed rows forward through a re-run, as before: a row
    someone typed is not model output and a rebuild must not eat it.
    """
    tabs = {t: [] for t in ACTION_TAB_ORDER}
    seen = {"exact": set(), "tokens": {}}
    counts = {"fix": 0, "repeat": 0, UNROUTED: 0}

    for f in (fixes or []):
        if not isinstance(f, dict):
            continue
        text = str(f.get("action") or "").strip()
        if not text:
            continue                      # a fix with no action is not a fix
        if _is_repeat(text, seen, "action"):
            counts["repeat"] += 1
            continue
        _remember(text, seen, "action")
        owner = str(f.get("owner") or "").strip().lower()
        owner = FLAG_TEAM_ALIASES.get(owner, owner)
        tab = owner if owner in ACTION_TEAMS else UNROUTED
        tabs[tab].append(text)
        counts["fix"] += 1
        if tab == UNROUTED:
            counts[UNROUTED] += 1

    # THE FLAGS DO NOT COME IN HERE, by request.
    #
    # They were routed in as actions and produced rows like "No Headout
    # process required monitoring SP-initiated time-change communications" and
    # "Nobody was required to contact the guest proactively" — the ABSENCE of
    # an action, filed under Actions Taken, indistinguishable from a row
    # someone had performed. A flag is what went wrong; it is already a
    # finding in the Flags section and does not need a second home.
    #
    # What remains is what the heading can honestly carry: the fixes, and the
    # rows a person typed.

    kept = 0
    for t, items in (keep or {}).items():
        if t not in tabs:
            continue
        for row in (items or []):
            txt = str(row or "").strip()
            if txt and not _is_repeat(txt, seen, "action"):
                _remember(txt, seen, "action")
                tabs[t].append(txt)
                kept += 1

    notes = []
    if kept:
        notes.append(f"actions taken: {kept} hand-added row(s) carried forward "
                     f"through this re-run")
    if counts["repeat"]:
        # A judgement, announced: two fixes worded differently were treated as
        # one, and nothing else on the card would say so.
        notes.append(f"actions taken: {counts['repeat']} fix(es) said what "
                     f"another fix already said and were merged")
    if counts[UNROUTED]:
        notes.append(f"actions taken: {counts[UNROUTED]} fix(es) name no team "
                     f"and sit on the Unrouted tab — nobody picks those up "
                     f"until someone assigns them")
    if not counts["fix"] and not kept:
        # Distinguishable from a run that never looked: there was nothing to
        # route, which is a legitimate answer for a case needing no action.
        notes.append("actions taken: no fix in §3 and no row typed by hand, "
                     "so there is nothing for any team to pick up")

    return tabs, {"counts": counts, "kept": kept, "notes": notes,
                  "unrouted": counts[UNROUTED]}
