"""The server-side Slack formatter must produce the same v3 layout the
dashboard previews. Send posts THIS when the associate hasn't edited the
preview, so a drift here means what leadership reads differs from what the
analyst approved."""
from types import SimpleNamespace

from server.services.slack import format_rca_slack


V3 = {
    "tldr": {"our_mistake": "The page did not state the delivery window.",
             "our_fix": "Content is auditing the listing this week."},
    "what_went_wrong": {
        "guest_issues": [{"issue": "Tickets arrived later than expected.",
                          "claim_accuracy": "Partially True",
                          "evidence": ["[experience-page] No window is stated.",
                                       "[zendesk] The email promised two hours."]}],
        "what_happened": {
            "root_causes": [{"issue": "Delay", "cause": "Selenium fulfilment ran without disclosure.",
                             "classification": "Operational + HO"}],
            "operational_failure": ["The chat went unanswered for nine minutes."],
            "sop_gap": ["No checkout warning exists for same-day Selenium bookings."],
            "pattern": "one-off - 0 similar reviews in 90d"},
        "sp_escalation": {"escalated": "N/A",
                          "detail": ["The vendor is not partnered.",
                                     "No escalation email is on file."]},
        "fixes": {"teams": ["Content", "Product"], "owner": "Content",
                  "actions": ["Audit the delivery window on TGID 22238."],
                  "prevention": ["Add a checkout callout for same-day Selenium."]},
    },
    "booking_logs": [
        {"time": "22 Jul 15:22", "what": "Booking-in-progress email sent",
         "detail": "The email promised tickets within two hours."},
        {"time": "22 Jul 15:50", "what": "Tickets issued", "detail": ""},
    ],
    "flags": [{"team": "content", "flag": "The delivery window is not on the page.",
               "evidence": "redemption is null", "zd_ref": "ZD-34011333"}],
    "support_interaction": [{"time": "15:41", "channel": "chat",
                             "summary": "The guest asked for tickets immediately.",
                             "ce_miss": "No agent replied for nine minutes.",
                             "zd_ref": "ZD-34011401"}],
    "sp_interaction": {"possible": False, "reason_if_not": "The vendor is not partnered.",
                       "raised": "N/A", "detail": [], "zd_ref": ""},
    "sop_compliance": {"dss_available": True, "verdict": "followed",
                       "expected": "Resend tickets or refund.",
                       "actual": "A refund was issued after the guest persisted.",
                       "detail": "Denial, then persistence, then refund.",
                       "zd_ref": "ZD-34011333"},
    "takedown": {"recommended": False, "reason": "The claim is partially accurate."},
    "area_of_improving": ["Surface the delivery window at checkout."],
}


def _draft(**over):
    base = dict(
        booking={"id": "32908218"}, rca_v3=V3, l1="Operations Issue",
        l2="Ticket Issues", sub_theme="C. Ticket Delayed",
        primary_scenario="Tickets sent late", overlay_scenarios=["Refund issues"],
        wwr_scenarios=[], wwr_chain=[], support_interaction_frames=[],
        sp_interaction_frames=[], area_of_improving=[], actions_taken={},
        resolution="Full refund issued.", checklist_answers=[], tldr="",
        insights={"rating_30d": {"avg": 4.2, "n": 51}, "vidCompletionRate": "57%",
                  "_window_days": 90},
    )
    base.update(over)
    return SimpleNamespace(**base)


REVIEW = SimpleNamespace(rating=1, author="David")


def test_v3_layout_has_the_five_headings_and_events():
    out = format_rca_slack(REVIEW, _draft())
    for must in ("1. Guest issue", "2. Is the guest's claim accurate?",
                 "3. What actually happened?", "4. Supply Partner escalation",
                 "5. Fixes", "1. 22 Jul 15:22 — Booking-in-progress email sent",
                 "2. 22 Jul 15:50 — Tickets issued",
                 "*Booking logs*", "*Flags*", "*SOP compliance*",
                 "*Review takedown*", "*Experience insights*"):
        assert must in out, f"missing: {must!r}"


def test_pointer_lists_never_print_as_comma_runs():
    out = format_rca_slack(REVIEW, _draft())
    assert "[experience-page] No window is stated., " not in out
    assert "   - [experience-page] No window is stated." in out
    assert "   - The vendor is not partnered." in out


def test_flags_absent_reads_as_clean_not_as_missing():
    d = _draft(rca_v3={**V3, "flags": []})
    assert "No flags raised" in format_rca_slack(REVIEW, d)


def test_legacy_draft_still_uses_legacy_layout():
    d = _draft(rca_v3=None, wwr_chain=[{"step": 1, "what": "Booked", "why": "Guest paid."}])
    out = format_rca_slack(REVIEW, d)
    assert "1. *Booked* — Guest paid." in out
    assert "1. Guest issue" not in out


def test_no_guest_contact_is_stated_explicitly():
    d = _draft(rca_v3={**V3, "support_interaction": []})
    assert "No guest contact found on this booking" in format_rca_slack(REVIEW, d)
