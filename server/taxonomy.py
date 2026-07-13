"""
REPLACES server/taxonomy.py — v2 with full L1/L2 rules + sub-theme frameworks.

Source of truth for classification. Read by prompts.py and by validators
in services/claude.py.

STAKEHOLDER STATUS:
  L1/L2 catalogue + priority rules: RECEIVED from CX Lead
  MP sub-theme framework:           RECEIVED
  Ticket sub-theme framework:       RECEIVED
  Audio Guide sub-theme framework:  RECEIVED
  SP sub-theme framework:           RECEIVED
  Customer Support Issues framework: PENDING
  Content / Misleading Info framework: PENDING
  Venue closure sub-theme framework:  PENDING
  Guide No Show sub-theme framework:  PENDING (may reuse SP framework)
  Venue Related sub-theme framework:  PENDING
  External Factor sub-theme framework: PENDING
  Miscellaneous sub-theme framework:   PENDING
"""

# ═════════════════════════════════════════════════════════════════════════
# L1 CATEGORIES — priority order (higher = wins ties)
# ═════════════════════════════════════════════════════════════════════════
L1_PRIORITY_ORDER = [
    "Operations Issue",
    "Product Issue",
    "Supply Partner Issue",
    "Venue Related Issue",
    "Business Issue",
    "External Factor",
    "Miscellaneous Issue",
]
L1_CATEGORIES = list(L1_PRIORITY_ORDER)

# ═════════════════════════════════════════════════════════════════════════
# Within Operations Issue, L2 priority sub-order (higher = wins ties)
# ═════════════════════════════════════════════════════════════════════════
OPERATIONS_L2_PRIORITY_ORDER = [
    "Meeting Point Issues",
    "Ticket Issues",
    "Content - Instructions not clear / Misleading Info",
    "Customer expectation mismatch",
    "Customer Support Issues",
    "Inventory Listing Issue",
    "Venue closure",  # Only when Headout proactively communicated or unforeseeable
]

# ═════════════════════════════════════════════════════════════════════════
# Full L2 options per L1
# ═════════════════════════════════════════════════════════════════════════
L2_OPTIONS = {
    "Operations Issue": [
        "Meeting Point Issues",
        "Ticket Issues",
        "Content - Instructions not clear / Misleading Info",
        "Customer expectation mismatch",
        "Customer Support Issues",
        "Inventory Listing Issue",
        "Venue closure",
    ],
    "Product Issue": [
        "Audio Guide Issues",
        "App and Website Issues",
    ],
    "Supply Partner Issue": [
        "Guide No Show",
        "Guide providing irrelevant/inexperienced/not clear",
        "Guide Behaviour Issues",
        "Guide Left / Abandoned Tour",
        "Timing Issues",
        "Tour Cancelled by Operator",
        "Seating Issues",
        "Food & Catering",
    ],
    "Venue Related Issue": [
        "Venue facility issue",
        "Venue Overcrowding (Venue)",
        "Venue closure",
    ],
    "Business Issue": [
        "Pricing Issues",
    ],
    "External Factor": [
        "Customer Late",
        "Customer Error",
        "Weather Related",
        "Venue Overcrowding (External)",
        "Force Majeure",
        "Sold Free / Discounted Admission",
        "Rating Mismatch",
        "Gibberish / Profanity",
    ],
    "Miscellaneous Issue": [
        "Vague review",
        "Negative Headout",
        "General negative exp",
    ],
}

# ═════════════════════════════════════════════════════════════════════════
# Sub-theme frameworks — for L2s that have their own sub-classification
#
# Each framework: {
#   "exclusion": [keywords] -> maps to exclusion_label,
#   "exclusion_label": "H. Irrelevant" or similar,
#   "sub_themes": [(code, name, cue_keywords_list), ...] in priority order,
# }
# ═════════════════════════════════════════════════════════════════════════

MP_SUB_THEMES = {
    "l2_key":         "Meeting Point Issues",
    "exclusion":      ["long queue", "waiting time", "crowding", "congestion",
                        "overcrowding", "high pricing"],
    "exclusion_label": "G. Irrelevant",
    "sub_themes": [
        ("A", "Meeting Point Instruction Mismatches",
            ["wrong address", "incorrect google maps", "inconsistent location",
             "voucher and email showing different", "incorrect meeting location"]),
        ("B", "Contact Issues",
            ["contact number missing", "no phone number", "phone number not working",
             "could not reach operator"]),
        ("C", "Guide or Host Issues",
            ["guide absent", "host not present", "guide standing elsewhere",
             "could not identify guide", "guide left before customer arrived",
             "no one at meeting point", "pickup did not arrive",
             "operator abandoned", "guide didn't show up"]),
        ("D", "Meeting Point Instructions Unclear",
            ["unclear instructions", "confusing directions", "could not understand where to go",
             "no clear landmark", "generic instructions", "difficult to locate"]),
        ("E", "Conflicting or Incorrect Timing Information",
            ["time changed", "rescheduled without notice", "wrong time provided",
             "last-minute time change", "unclear timing"]),
        ("F", "Any Other Meeting Point Issue", []),
    ],
}

TICKET_SUB_THEMES = {
    "l2_key":         "Ticket Issues",
    "exclusion":      ["long queue", "waiting time", "crowding", "congestion",
                        "overcrowding", "high pricing", "venue closed", "closure"],
    "exclusion_label": "H. Irrelevant",
    "sub_themes": [
        ("A", "Ticket Invalid / Not Working",
            ["ticket did not work", "qr code not scanning", "barcode failed",
             "ticket already redeemed", "invalid ticket", "could not enter using ticket",
             "entry denied due to ticket", "qr codes didn't work"]),
        ("B", "Ticket Not Received",
            ["ticket not received", "tickets were not sent", "did not receive ticket",
             "no ticket email", "ticket missing", "waiting but ticket not delivered",
             "booking not processed"]),
        ("C", "Ticket Delayed",
            ["ticket received late", "last minute ticket delivery",
             "tickets sent just before entry", "delay in ticket delivery"]),
        ("D", "Wrong Ticket (Date / Time / Variant)",
            ["wrong date ticket", "wrong time ticket", "different experience than booked",
             "incorrect ticket issued", "booking not honored correctly",
             "shifted to", "different variant", "only got park", "combo mismatch"]),
        ("E", "Alts related",
            ["accepted a different date", "agreed to reschedule",
             "issue related to previously accepted change"]),
        ("F", "Ticket Instructions / Information Unclear",
            ["unclear ticket redemption", "confusing redemption steps",
             "missing ticket instructions", "no clear entry process"]),
        ("G", "Any Other Ticket Issue", []),
    ],
    # Tiebreak: if no ticket produced -> B; if ticket produced but wrong -> D
    "tiebreak_rule": "B if no ticket produced; D if wrong ticket produced",
}

AG_SUB_THEMES = {
    "l2_key":         "Audio Guide Issues",
    "exclusion":      ["internet issues", "waiting time", "long queue",
                        "overcrowding", "downloading issues"],
    "exclusion_label": "G. Irrelevant",
    "sub_themes": [
        ("A", "AG Not Sent / Not Received",
            ["audio guide not sent", "never received audio guide link",
             "audio guide purchased but not delivered"]),
        ("B", "AG App / Technical Issues",
            ["app not opening", "app crashing", "app stuck", "audio guide lagging",
             "stopped mid-tour", "login not working"]),
        ("C", "AG Redemption Instruction Issues",
            ["unclear audio guide activation", "activation steps missing"]),
        ("D", "AG Language Issues",
            ["selected language not available", "wrong language playing",
             "language option missing", "not in selected language"]),
        ("E", "AG Quality & Content Issues",
            ["poor narration", "low audio quality", "shallow explanation",
             "irrelevant content", "incomplete content", "not in sync with venue",
             "navigation issues", "boring narration"]),
        ("F", "Any Other Audio Guide Issue", []),
    ],
    "tiebreak_rule": "A if never obtained; B if obtained then failed during use",
}

SP_SUB_THEMES = {
    "l2_key":         None,  # Applies across all SP-level L2s
    "applies_to_l2":  ["Guide No Show", "Guide providing irrelevant/inexperienced/not clear",
                        "Guide Behaviour Issues", "Guide Left / Abandoned Tour",
                        "Timing Issues", "Tour Cancelled by Operator"],
    "exclusion":      ["long queue", "waiting time", "crowding", "congestion",
                        "overcrowding", "high pricing", "venue closure", "venue closed"],
    "exclusion_label": "H. Irrelevant",
    "sub_themes": [
        ("A", "Guide No Show",
            ["guide never arrived", "nobody at meeting point", "guide did not turn up",
             "guide did not appear"]),
        ("B", "Guide Left Mid Tour",
            ["guide abandoned the group", "guide left in the middle", "walked off midway"]),
        ("C", "Guide Behaviour Issue",
            ["rude guide", "unprofessional guide", "impolite guide"]),
        ("D", "Guide Timing Issue",
            ["tour started late", "time changed", "started 45 mins late"]),
        ("E", "Guide Quality Issue",
            ["poor explanation", "bad guiding", "guide unclear",
             "guide couldn't answer questions"]),
        ("F", "Contact Issue",
            ["phone unreachable", "operator not responding", "could not reach"]),
        ("G", "Other Supply Partner Issue", []),
    ],
}

# Registry: which sub-theme framework applies to which L1/L2
SUB_THEME_REGISTRY = {
    ("Operations Issue", "Meeting Point Issues"): MP_SUB_THEMES,
    ("Operations Issue", "Ticket Issues"):        TICKET_SUB_THEMES,
    ("Product Issue",    "Audio Guide Issues"):   AG_SUB_THEMES,
    # SP-wide framework — one framework covers many L2s
    ("Supply Partner Issue", "Guide No Show"):                                    SP_SUB_THEMES,
    ("Supply Partner Issue", "Guide providing irrelevant/inexperienced/not clear"): SP_SUB_THEMES,
    ("Supply Partner Issue", "Guide Behaviour Issues"):                           SP_SUB_THEMES,
    ("Supply Partner Issue", "Guide Left / Abandoned Tour"):                      SP_SUB_THEMES,
    ("Supply Partner Issue", "Timing Issues"):                                    SP_SUB_THEMES,
    ("Supply Partner Issue", "Tour Cancelled by Operator"):                       SP_SUB_THEMES,
}


# ═════════════════════════════════════════════════════════════════════════
# Diagnostic checks per L1 — for the RCA panel
# ═════════════════════════════════════════════════════════════════════════
DIAGNOSTIC_CHECKS = {
    "Operations Issue": [
        {"key": "tickets_sent_on_time", "question": "Were tickets sent on time?",
         "data_source": "timeline.tickets_sent"},
        {"key": "voucher_matches_experience", "question": "Did the voucher match the experience booked?",
         "data_source": "booking + timeline"},
        {"key": "meeting_point_info_correct", "question": "Was the meeting point info on the voucher correct?",
         "data_source": "voucher content"},
        {"key": "ce_handled_correctly", "question": "Did CE handle the case within playbook?",
         "data_source": "timeline.co_events"},
    ],
    "Supply Partner Issue": [
        {"key": "sp_informed_proactively", "question": "Did the SP inform Headout of the issue proactively?",
         "data_source": "timeline.sp_events"},
        {"key": "guide_showed_up", "question": "Did the guide show up at the meeting point?",
         "data_source": "review_text + timeline"},
        {"key": "retry_available", "question": "Was a retry / fallback available at the venue?",
         "data_source": "timeline.sp_events"},
        {"key": "ce_escalated", "question": "Did CE escalate to the SP?",
         "data_source": "timeline.co_events"},
    ],
    "Product Issue": [
        {"key": "tickets_sent_on_time", "question": "Were tickets sent on time?",
         "data_source": "timeline.tickets_sent"},
        {"key": "audio_guide_provisioned", "question": "Was the audio guide provisioned correctly?",
         "data_source": "booking + timeline"},
    ],
    "Venue Related Issue": [
        {"key": "venue_condition_pre_flagged", "question": "Was the venue condition pre-flagged internally?",
         "data_source": "insights + historical reviews"},
    ],
    "Business Issue": [
        {"key": "pricing_disclosed", "question": "Was the price transparently disclosed at checkout?",
         "data_source": "booking flow"},
    ],
    "External Factor": [],
    "Miscellaneous Issue": [],
}

# ═════════════════════════════════════════════════════════════════════════
# Support Interaction gap taxonomy
# ═════════════════════════════════════════════════════════════════════════
GAP_TAXONOMY = [
    "Minded AI wrong canned response",
    "Minded AI missed escalation trigger",
    "CE delay — no action within SLA",
    "CE escalation missing",
    "Wrong policy applied",
    "SP not looped in",
    "Chargeback initiated",
    "TAT breach",
]

# ═════════════════════════════════════════════════════════════════════════
# Signal extraction fields
# ═════════════════════════════════════════════════════════════════════════
SIGNAL_FIELDS = [
    "guest_name",
    "experience_hint",
    "venue_or_city",
    "visit_date_hint",
    "group_size",
    "issue_summary",
]

# ═════════════════════════════════════════════════════════════════════════
# Action Taken tabs
# ═════════════════════════════════════════════════════════════════════════
ACTION_TABS = {
    "sp":       {"label": "SP",       "default_handle": "[SP handle placeholder]"},
    "customer": {"label": "Customer", "default_handle": "[CE handle placeholder]"},
    "business": {"label": "Business", "default_handle": "[Biz handle placeholder]"},
    "product":  {"label": "Product",  "default_handle": "[Product handle placeholder]"},
    "ce":       {"label": "CE",       "default_handle": "[CE handle placeholder]"},
}

# ═════════════════════════════════════════════════════════════════════════
# Similar complaints match rule
# ═════════════════════════════════════════════════════════════════════════
SIMILAR_MATCH_RULE = {
    "match_on":    ["tid", "vid"],
    "same_l1":     True,
    "window_days": 30,
    "max_results": 5,
}


# ═════════════════════════════════════════════════════════════════════════
# BID regex — widened to accept 7-12 digits (Angela's review had 11-digit BID)
# ═════════════════════════════════════════════════════════════════════════
BID_REGEX = r'\b\d{7,12}\b'


# ═════════════════════════════════════════════════════════════════════════
# Validators — the cheap "confidence gate" so hallucinated output doesn't
# poison downstream steps. These are what give you multi-agent resilience
# without the multi-agent cost.
# ═════════════════════════════════════════════════════════════════════════
def is_valid_l1(l1: str) -> bool:
    return l1 in L1_CATEGORIES


def is_valid_l1_l2(l1: str, l2: str) -> bool:
    return l1 in L1_CATEGORIES and l2 in L2_OPTIONS.get(l1, [])


def is_valid_sub_theme(l1: str, l2: str, sub_theme_code: str) -> bool:
    fw = SUB_THEME_REGISTRY.get((l1, l2))
    if not fw:
        return sub_theme_code in (None, "", "N/A")
    valid_codes = [f"{code}. {name}" for code, name, _ in fw["sub_themes"]]
    valid_codes.append(fw["exclusion_label"])
    return sub_theme_code in valid_codes


def has_sub_theme_framework(l1: str, l2: str) -> bool:
    return (l1, l2) in SUB_THEME_REGISTRY


def sub_theme_framework(l1: str, l2: str):
    return SUB_THEME_REGISTRY.get((l1, l2))


def l2_options_for(l1: str) -> list:
    return L2_OPTIONS.get(l1, [])


def checks_for(l1: str) -> list:
    return DIAGNOSTIC_CHECKS.get(l1, [])
