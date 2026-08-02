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
  Customer Support Issues framework: RECEIVED
  Content / Misleading Info framework: RECEIVED
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

# ─────────────────────────────────────────────────────────────────────────
# Content / Misleading Info — sub-theme categorization from VS "Content Issues
# - Samson Copy" (13-theme structure). Text-based classifier: runs on the
# review text alone, exactly like MP/SP/Ticket/AG.
#
# What was taken vs dropped: only the sub-theme CATEGORIZATION is ported. The
# VS pipeline's metadata-validation stages (comparing the review against
# BigQuery content fields — inclusions/faq/validity/etc.) are NOT ported, so:
#   - "Unsupported Content Claim" (a metadata state) is not a text sub-theme here.
#   - E "Missing FAQ/KBYG" vs F "Misleading FAQ/KBYG" may blur from text alone,
#     since the true distinction is whether the faq field exists (metadata).
# Codes A–M are assigned for registry/validator uniformity (source emits the
# plain names). "N. Irrelevant" is the exclusion state.
# ─────────────────────────────────────────────────────────────────────────
CONTENT_SUB_THEMES = {
    "l2_key":         "Content - Instructions not clear / Misleading Info",
    "exclusion":      ["long queues", "crowding", "weather", "guide behaviour",
                       "guide quality", "customer support", "app bugs",
                       "cancellations by operator", "timing delays",
                       "customer mistakes", "late arrival", "transport",
                       "meeting point", "audio guide functionality",
                       "ticket delivery", "qr code issues", "ticket not working",
                       "ticket not received", "ticket received late",
                       "food quality/taste/quantity", "broken seats",
                       "venue facilities"],
    "exclusion_label": "N. Irrelevant",
    "sub_themes": [
        ("A", "Misleading Food Information",
            ["food/meal inclusion mismatch", "advertised food not provided"]),
        ("B", "Misleading Seating Information",
            ["seating category mismatch", "view different from advertised"]),
        ("C", "Misleading Inclusions",
            ["incorrect inclusions", "advertised benefit not provided"]),
        ("D", "Misleading Exclusions",
            ["incorrect exclusions", "unexpected exclusion"]),
        ("E", "Missing FAQ/KBYG Information",
            ["restriction not mentioned", "process detail missing", "faq field missing"]),
        ("F", "Misleading FAQ/KBYG Information",
            ["faq present but misleading", "kbyg contradicts experience"]),
        ("G", "Misleading Validity Information",
            ["incorrect validity", "validity not as stated"]),
        ("H", "Misleading Cancellation Information",
            ["cancellation policy unclear from content", "cancellation confusion"]),
        ("I", "Misleading Ticket Delivery Information",
            ["ticket delivery info wrong", "delivery method not as stated"]),
        ("J", "Misleading Confirmed Ticket Information",
            ["confirmed ticket info incorrect"]),
        ("K", "Hosted Entry / Fast-Track Content Mismatch",
            ["skip-the-line misleading", "hosted entry not as described", "priority entry mismatch"]),
        ("L", "Misleading Experience Description",
            ["description does not match experience", "highlights/summary misleading"]),
        ("M", "General Content Mismatch", []),
    ],
    "tiebreak_rule": "Prefer the most specific theme (food/seating/inclusions) over General Content Mismatch.",
}

CUSTOMER_SUPPORT_SUB_THEMES = {
    "l2_key":         "Customer Support Issues",
    "exclusion":      ["long queue", "waiting time", "crowding", "congestion",
                       "overcrowding", "high pricing", "venue closure", "venue closed"],
    "exclusion_label": "H. Irrelevant",
    "sub_themes": [
        ("A", "No Response / Ignored",
            ["no response", "no reply", "never replied", "filed a complaint never heard back",
             "multiple emails no response", "support unreachable", "chat kept cutting off"]),
        ("B", "Refund Denied / Delayed",
            ["refund denied", "refused to refund", "refund promised but not received",
             "still awaiting refund", "no refund despite cancellation",
             "refund delay past promised", "charged but not reimbursed", "bank chargeback refused"]),
        ("C", "Reschedule / Cancellation Request Denied",
            ["reschedule refused", "cannot change date", "emergency situation ignored",
             "medical emergency no help", "family emergency no help",
             "flight changed no accommodation", "strict cancellation no flexibility",
             "refused to move booking"]),
        ("D", "Wrong / Misleading Information Given by Support",
            ["support told me wrong info", "agent gave incorrect details",
             "false claims by support", "support said X but Y happened", "misled by CE"]),
        ("E", "Support Slow / Delayed Resolution",
            ["took days to resolve", "took weeks to get a response",
             "long wait for support reply", "eventually resolved but far too late"]),
        ("F", "Rude / Unprofessional Support",
            ["rude agent", "dismissive support", "disrespectful CE",
             "insulted by support", "unprofessional handling"]),
        ("G", "Any Other Customer Support Issue", []),
    ],
    "tiebreak_rule": "A if support never engaged even on refund; B only if support engaged and denied",
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
    ("Operations Issue", "Content - Instructions not clear / Misleading Info"):   CONTENT_SUB_THEMES,
    ("Operations Issue", "Customer Support Issues"):                              CUSTOMER_SUPPORT_SUB_THEMES,
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


# ── Support tag map for Experience Insights ──────────────────────────────────
# Keywords come verbatim from the four finalized VectorShift pipelines.
# Values are either list[str] (exact-tag match via IN UNNEST) or
# {"like_any": [...]} (LIKE-any variant for Content L2).

SUPPORT_TAG_MAP = {
    ("Operations Issue", "Meeting Point Issues"): [
        "Ticket Redemption Details  Meeting Point Related  Meeting Point Details Requested",
        "Ticket Redemption Details  Sp Information",
        "Ticket Redemption Details  Meeting Point Related  Meeting Point Is Incorrect/missing",
        "Ticket Redemption Details  Transfer / Pick Up Related",
        "Service Issues  Sp Related  Guide Was Late/didn T Arrive",
        "Service Issues  Sp Related  Guide Issues",
    ],
    ("Operations Issue", "Ticket Issues"): [
        "Delay Fulfilment Ticket Related Issues Sp Related",
        "Ticket Redemption Details Customer Complaint Already Redeemed Tickets",
        "Ticket Redemption Details  Meeting Point Related  Meeting Point Is Incorrect/missing",
        "Delay Fulfilment Ticket Related Issues Guest Names Are Missing",
        "Delay Ticket Related Issues Guest Names Are Missing",
    ],
    ("Product Issue", "Audio Guide Issues"): [
        "Audio Guide Redemption Issue",
        "Ticket Redemption Details  Audio Guide Related  Tech Issues",
        "Ticket Redemption Details  Audio Guide Related  Redemption Information/issues",
    ],
    # DELIBERATE, and confirmed: every Supply Partner L2 shares this one tag
    # set, exactly as the VectorShift brief specifies. Do not split it.
    #
    # The consequence is visible and looks like a bug: all eight SP L2s report
    # the same support count, so Food & Catering claims the same history as
    # Guide No Show, including 8,249 Sp Dependency delay contacts. The tags do
    # divide cleanly by L2 - Tour Cancelled By Sp, Guide Was Late, Offered A
    # Different Time each belong to an obvious one - and splitting them was
    # proposed and declined.
    #
    # The reasoning to preserve: for a supply partner the question is whether
    # THE VENDOR is failing, not which flavour of failure this ticket was. SP
    # problems bleed into each other - a late guide becomes a cancelled tour
    # becomes a delay - and narrowing to one L2 would hide a vendor that is
    # failing steadily in three different ways at once.
    #
    # Note this is the opposite call from the review side, where the guide L2s
    # were deliberately un-collapsed. That is not an inconsistency: a review is
    # one guest describing one visit, and a support tag set is a vendor's
    # operating record.
    ("Supply Partner Issue", "__all__"): [
        "Service Issues  Sp Related  Guide Was Late/didn T Arrive",
        "Delay Fulfilment  Ticket Related Issues  Sp Related",
        "Service Issues  Sp Related  Tour Language Was Different",
        "Modification Request  Sp Related  Strike/venue Closure",
        "Service Issues  Sp Related  Guide Issues",
        "Delay  Delay Fulfilment  Sp Dependency",
        "Service Issues  Sp Related  Tour Cancelled By Sp",
        "Delay Fulfilment  Delay  Sp Dependency",
        "Modification Request  Sp Related  Offered A Different Time",
    ],
    # This L2 means "we told the guest something wrong or unclear". Narrowed
    # against 180 days of real query_tag values, where the ten patterns as
    # written matched 161,939 of 464,295 rows - 35% of every support contact
    # Headout receives. A number that large is not "similar issues", it is
    # "any issue", and it made this L2's tile indistinguishable from the total.
    #
    # Dropped, too broad:
    #   %cancellation%  55,183 - overwhelmingly cancellation REQUESTS. Wanting
    #                   to cancel is not a complaint about what we published.
    #   %information%   74,688 - matches whole unrelated families, including
    #                   "Ticket Redemption Details  Sp Information" and the
    #                   Audio Guide "Redemption Information/issues" tags that
    #                   the Meeting Point and Audio Guide L2s already own. It
    #                   was the main reason two different issues returned
    #                   identical counts.
    #
    # Dropped, matched nothing at all - the tag vocabulary does not use these
    # words, so they were never doing anything:
    #   %content%  %description%  %website%  %incorrect details%  %misleading%
    #
    # Kept - live, and genuinely about what was published rather than what
    # happened. Together 32,068 rows, 6.9% of support contacts, which is a
    # plausible share for one issue type:
    #   %inclusion%  17,092    what the ticket said was included
    #   %exclusion%  11,389    what it said was not
    #   %validity%    3,587    when the ticket was said to be valid
    #
    # An exact list would be better still, as the other four L2s have. That
    # needs the full 106 live tag values eyeballed once; verify_taxonomy now
    # prints the ones nothing maps to.
    # ── mapped from the live query_tag vocabulary, 180 days ──────────────
    # The five above came verbatim from the VectorShift brief. These twenty
    # were unmapped, which is not the same as having no support history - it
    # meant the tile read zero by construction. Each tag below is a live value
    # with its row count; an L2 with no honest counterpart is left out rather
    # than mapped to something adjacent, because a wrong tag attributes one
    # issue's support history to another.

    ("Operations Issue", "Inventory Listing Issue"): [
        "Cancellation Request  Headout Related  Inventory Issues",   # 2,605
        "Delay Fulfilment  Delay  Inventory Issue",                  #   661
        "Modification Request  Headout Related  Inventory Issues",   #   601
    ],
    ("Operations Issue", "Venue closure"): [
        "Cancellation Request  Sp Related  Strike/venue Closure",    # 5,108
        "Modification Request  Sp Related  Strike/venue Closure",    #   371
    ],
    ("Operations Issue", "Customer expectation mismatch"): [
        # What they were told they would get, against what they got.
        "General Information  Experience Related  Inclusions/exclusions Related",  # 5,883
        "Ticket Redemption Details  Inclusions/exclusions Related",  # 5,506
        "Refund Related  Refund Request  Inclusions Not Met In The Experience",    # 4,174
        "Service Issues  Experience Related  Unsatisfactory Tour Experience",      # 1,976
        "Service Issues  Experience Related  Inclusions Not Met/closed",           # 1,529
    ],
    ("Venue Related Issue", "Venue closure"): [
        "Cancellation Request  Sp Related  Strike/venue Closure",    # 5,108
        "Modification Request  Sp Related  Strike/venue Closure",    #   371
    ],
    ("Business Issue", "Pricing Issues"): [
        "General Information  Pricing Related  Offers/discount",     # 3,646
        "Refund Related  Refund Request  Price Disparity",           # 3,199
        "General Information  Pricing Related  Convenience Fee",     #   428
        "General Information  Pricing Related  Others",              #   370
        "Cancellation Request  Customer Related  Found Cheaper Options",  # 352
        "General Information  Pricing Related  Conversion Fee",      #    42
    ],
    ("External Factor", "Customer Error"): [
        "Cancellation Request  Customer Related  Customer Error",    # 11,411
        "Modification Request  Customer Related  Wrong Date/time",   # 11,327
        "Delay Fulfilment  Ticket Related Issues  Booked With Wrong Email",  # 4,938
        "Modification Request  Customer Related  Wrong Pax",         #  2,484
        "Cancellation Request  Customer Related  Booked Twice",      #  1,731
    ],
    ("External Factor", "Weather Related"): [
        "Cancellation Request  Headout Related  Weather Related",    # 1,696
        "Modification Request  Headout Related  Weather Related",    #   649
    ],
    ("External Factor", "Force Majeure"): [
        # Strikes and sudden venue closure are force majeure as much as they
        # are a closure, so they appear here and under Venue closure. Two L2s
        # legitimately drawing on one tag is not double counting - each asks a
        # different question of the same contact.
        "Cancellation Request  Sp Related  Strike/venue Closure",    # 5,108
        "Modification Request  Sp Related  Strike/venue Closure",    #   371
        "War Conflict 2026",                                         #    86
    ],
    ("Product Issue", "App and Website Issues"): [
        # Checkout failing IS the website failing, from the guest's side.
        "Payment Failure  Sift/fingerprint Block",                   # 1,198
        "Payment Failure  3ds Failure",                              #   916
    ],

    # Deliberately unmapped pairs are DATA, not a comment - see
    # SUPPORT_TAGS_NOT_APPLICABLE below.

    ("Operations Issue", "Content - Instructions not clear / Misleading Info"): {
        "like_any": [
            "%inclusion%", "%exclusion%", "%validity%",
        ],
    },
}


# Pairs with no support tag ON PURPOSE, and the reason for each.
#
# This was a comment. The card said "no support-tag mapping for Operations
# Issue / Customer Support Issues - support contacts not compared", which reads
# as a thing somebody forgot to do, and it is not: there is no contact-reason
# tag for "unhappy with how we handled it", because that is not why anyone
# opens a ticket. A deliberate absence and an unfilled gap produced the same
# sentence - the failure this codebase's first rule is about, in the other
# direction: a correct run reported as a broken one.
#
# Mapping any of these to an adjacent tag would invent history, so the answer
# is not to fill them in. It is to say WHY the comparison cannot be made.
SUPPORT_TAGS_NOT_APPLICABLE = {
    ("Operations Issue", "Customer Support Issues"):
        "guests contact us ABOUT something; being unhappy with the handling is "
        "not a contact reason, so no tag records it",
    ("Venue Related Issue", "Venue facility issue"):
        "the contact-reason vocabulary has no facility tag",
    ("Venue Related Issue", "Venue Overcrowding (Venue)"):
        "the contact-reason vocabulary has no queue or crowd tag",
    ("External Factor", "Venue Overcrowding (External)"):
        "the contact-reason vocabulary has no queue or crowd tag",
    ("External Factor", "Customer Late"):
        "arriving late is not something guests raise a ticket about",
    ("External Factor", "Sold Free / Discounted Admission"):
        "no contact-reason tag records what the venue charged at the door",
    ("External Factor", "Rating Mismatch"):
        "this describes a review, not a reason for contacting support",
    ("External Factor", "Gibberish / Profanity"):
        "this describes a review, not a reason for contacting support",
    ("Miscellaneous Issue", "Vague review"):
        "this describes a review, not a reason for contacting support",
    ("Miscellaneous Issue", "Negative Headout"):
        "this describes a review, not a reason for contacting support",
    ("Miscellaneous Issue", "General negative exp"):
        "this describes a review, not a reason for contacting support",
}


def support_tags_not_applicable(l1: str, l2: str) -> str:
    """Why this pair has no support-tag mapping, or "" if that is a real gap.

    "" is not "no reason" - it means the absence is unexplained, which is a
    different fact from an explained one and has to stay tellable apart.
    """
    return SUPPORT_TAGS_NOT_APPLICABLE.get((l1 or "", l2 or ""), "")


def support_tags_for(l1: str, l2: str):
    """Returns list[str], {'like_any': [...]}, or None."""
    if l1 == "Supply Partner Issue":
        return SUPPORT_TAG_MAP.get(("Supply Partner Issue", "__all__"))
    return SUPPORT_TAG_MAP.get((l1, l2))
