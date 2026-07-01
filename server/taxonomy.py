"""
ADD as server/taxonomy.py

The single source of truth for L1/L2 classification and diagnostic checks.
This file is READ from Claude prompts and API responses.

┌────────────────────────────────────────────────────────────────────────┐
│ STAKEHOLDER INPUT REQUIRED — CX Lead                                   │
│                                                                        │
│ Everything here is placeholder based on the demo. Before Sprint 1 you  │
│ need to replace the placeholder values with the real CX taxonomy.      │
│                                                                        │
│ Editing this file is safe — the API + prompts read from these dicts   │
│ dynamically. No other code changes needed.                             │
└────────────────────────────────────────────────────────────────────────┘
"""

# ─────────────────────────────────────────────────────────────────────────
# L1 categories — top-level RCA classification
# ─────────────────────────────────────────────────────────────────────────
L1_CATEGORIES = [
    "SP issue",
    "Customer error",
    "CO miss",
    "Product/UX",
    "Payment",
    "Untraceable",
    "Other",
]

# ─────────────────────────────────────────────────────────────────────────
# L2 sub-categories per L1
# TODO(cx-lead): Confirm/expand the list under each L1.
# ─────────────────────────────────────────────────────────────────────────
L2_OPTIONS = {
    "SP issue": [
        "Venue closure",
        "Did not receive tickets",
        "Redemption denied at venue",
        "SP did not pick up calls",
        "SP cancelled last-minute",
        "Wrong tickets issued",
        "SP system outage",
        # TODO(cx-lead): add more
    ],
    "Customer error": [
        "Misunderstood inclusions (upsell not selected)",
        "Wrong date/time selected",
        "Missed the tour",
        "Wrong location",
        # TODO(cx-lead): add more
    ],
    "CO miss": [
        "Minded AI wrong canned response",
        "Minded AI wrong reschedule guidance",
        "CE delay",
        "No escalation raised",
        "TAT breach",
        # TODO(cx-lead): add more
    ],
    "Product/UX": [
        "Misleading listing name",
        "Confusing checkout flow",
        "Missing information on PDP",
        # TODO(cx-lead): add more
    ],
    "Payment": [
        "Failed payment",
        "Duplicate charge",
        "Refund delay",
        # TODO(cx-lead): add more
    ],
    "Untraceable": [
        "No BID + no signals",
        "BigQuery returned no candidates",
    ],
    "Other": [
        "General complaint",
    ],
}

# ─────────────────────────────────────────────────────────────────────────
# Diagnostic checks — one list per L1
#
# Structure: {"key": "unique_id", "question": "...", "data_source": "..."}
# The AI runs each check by looking up the data_source in the context bundle
# (booking record, timeline events, insights) and returning yes/no.
#
# TODO(cx-lead): Confirm the full check list per L1 with CX Ops.
# ─────────────────────────────────────────────────────────────────────────
DIAGNOSTIC_CHECKS = {
    "SP issue": [
        {"key": "tickets_sent_on_time",     "question": "Were tickets sent on time?",                            "data_source": "timeline.tickets_sent"},
        {"key": "guest_arrived_on_time",    "question": "Did the guest arrive at the meeting point on time?",   "data_source": "timeline + review_text"},
        {"key": "sp_informed_proactively",  "question": "Did the SP inform Headout of the cancellation proactively?", "data_source": "timeline.sp_events"},
        {"key": "retry_fallback_available", "question": "Was a retry / fallback path available at the venue?",  "data_source": "timeline.sp_events"},
        {"key": "minded_ai_handled",        "question": "Did Minded AI handle the email correctly?",            "data_source": "timeline.ai_events"},
        {"key": "ce_escalated",             "question": "Did CE escalate to the SP on the chat thread?",        "data_source": "timeline.co_events"},
    ],
    "Customer error": [
        {"key": "tickets_sent_on_time",  "question": "Were tickets sent on time?",                                          "data_source": "timeline.tickets_sent"},
        {"key": "voucher_matches",       "question": "Does the voucher match what the guest received at the venue?",       "data_source": "booking + timeline"},
        {"key": "sp_applied_policy",     "question": "Did the SP apply policy correctly?",                                  "data_source": "timeline.sp_events"},
        {"key": "ce_explained_accurately","question": "Did the CE explain the booking accurately?",                         "data_source": "timeline.co_events"},
        {"key": "upsell_selected",       "question": "Did the guest select the relevant upsell at checkout?",              "data_source": "booking.upsells_applied"},
    ],
    "CO miss": [
        # TODO(cx-lead): populate
        {"key": "tat_breach",            "question": "Was TAT breached?",                                                  "data_source": "timeline"},
        {"key": "correct_playbook",      "question": "Was the correct playbook followed?",                                 "data_source": "timeline.co_events"},
    ],
    "Product/UX": [
        # TODO(cx-lead): populate
        {"key": "listing_matches",       "question": "Does the listing name accurately describe what's included?",        "data_source": "booking.experience_name"},
    ],
    "Payment": [
        # TODO(cx-lead): populate
    ],
    "Untraceable": [],
    "Other":       [],
}

# ─────────────────────────────────────────────────────────────────────────
# Support Interaction gap taxonomy — labelled gap types
#
# When Claude summarises a support interaction frame and detects an issue,
# it must tag it with one of these values (or omit if no gap).
#
# TODO(cx-lead): Confirm final list.
# ─────────────────────────────────────────────────────────────────────────
GAP_TAXONOMY = [
    "Minded AI wrong canned response",
    "Minded AI missed escalation trigger",
    "CE delay — no action within SLA",
    "CE escalation missing",
    "Wrong policy applied",
    "SP not looped in",
    "Chargeback initiated",
    "TAT breach",
    # TODO(cx-lead): add more
]

# ─────────────────────────────────────────────────────────────────────────
# Signal extraction — the fields Claude pulls from review text when no BID.
#
# TODO(cx-lead): Confirm/expand these.
# ─────────────────────────────────────────────────────────────────────────
SIGNAL_FIELDS = [
    "guest_name",
    "experience_hint",   # e.g. "Vatican Museums", "Eiffel summit"
    "venue_or_city",
    "visit_date_hint",   # any date phrases; may be "today", "yesterday"
    "group_size",
    "issue_summary",     # 1-line: what went wrong from guest's POV
]

# ─────────────────────────────────────────────────────────────────────────
# Action Taken tabs → which team lives under each tab.
# Handles are placeholders — replace with real Slack handles.
#
# TODO(cx-lead): confirm team names + real Slack handles per tab.
# ─────────────────────────────────────────────────────────────────────────
ACTION_TABS = {
    "sp":       {"label": "SP",       "default_handle": "[SP handle placeholder]"},
    "customer": {"label": "Customer", "default_handle": "[CE handle placeholder]"},
    "business": {"label": "Business", "default_handle": "[Biz handle placeholder]"},
    "product":  {"label": "Product",  "default_handle": "[Product handle placeholder]"},
    "ce":       {"label": "CE",       "default_handle": "[CE handle placeholder]"},
}

# ─────────────────────────────────────────────────────────────────────────
# Similar complaints matching rule
#
# TODO(cx-lead): Confirm — currently: same TID+VID, same L1, last 30 days.
# ─────────────────────────────────────────────────────────────────────────
SIMILAR_MATCH_RULE = {
    "match_on":    ["tid", "vid"],   # BigQuery fields to match on
    "same_l1":     True,              # also require same L1 classification
    "window_days": 30,
    "max_results": 5,
}


def is_valid_l1_l2(l1: str, l2: str) -> bool:
    return l1 in L1_CATEGORIES and l2 in L2_OPTIONS.get(l1, [])


def l2_options_for(l1: str) -> list:
    return L2_OPTIONS.get(l1, [])


def checks_for(l1: str) -> list:
    return DIAGNOSTIC_CHECKS.get(l1, [])
