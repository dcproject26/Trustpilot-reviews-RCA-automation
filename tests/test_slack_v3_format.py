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


def test_the_twelve_sections_still_frame_the_post():
    """The section structure and the include-picker keys are unchanged by v4 —
    only the What went wrong body was rebuilt."""
    out = format_rca_slack(REVIEW, _draft())
    for must in ("*What went wrong*", "1. 22 Jul 15:22 — Booking-in-progress email sent",
                 "2. 22 Jul 15:50 — Tickets issued",
                 "*Booking logs*", "*Flags*", "*SOP compliance*",
                 "*Review takedown*", "*Experience insights*"):
        assert must in out, f"missing: {must!r}"


def test_pointer_lists_never_print_as_comma_runs():
    out = format_rca_slack(REVIEW, _draft())
    assert "[experience-page] No window is stated., " not in out
    assert "   - [experience-page] No window is stated." in out
    assert "   - The vendor is not partnered." in out


def test_a_pre_v4_draft_keeps_its_document_level_analysis():
    """Its root causes, op failure and SOP gap live under what_happened, not
    on an issue. Rendering only the v4 shape would show the heading with
    nothing under it for every RCA written before this deploy."""
    out = format_rca_slack(REVIEW, _draft())
    for must in ("Selenium fulfilment ran without disclosure.",
                 "The chat went unanswered for nine minutes.",
                 "No checkout warning exists for same-day Selenium bookings.",
                 "Audit the delivery window on TGID 22238."):
        assert must in out, f"a legacy draft lost: {must!r}"


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


# ── v4: one block per guest issue ───────────────────────────────────────────

V4 = {
    "tldr": {"our_mistake": "We did not send the voucher.",
             "our_fix": "Refunded in full."},
    "what_went_wrong": {"guest_issues": [
        {"issue": "Voucher never delivered",
         "claim": "I waited two hours and nothing came.",
         "claim_accuracy": "Accurate",
         "claim_accuracy_note": "The fulfilment log confirms it.",
         "owner": "RO",
         "root_cause": "The fulfilment run failed silently.",
         "operational_failure": "No one watched the retry queue.",
         "sop_gap": "No alert exists on a failed fulfilment.",
         "evidence": [{"text": "Three failed retries, no page.",
                       "source": "bms", "ref": "ZD-4491"}]},
        {"issue": "Nobody answered the chat",
         "claim": "I messaged twice and got nothing.",
         "claim_accuracy": "Partly accurate", "owner": "CE",
         "root_cause": "First reply landed 40 minutes in.",
         "fix": "CE: staff the evening chat queue (owner: CE)",
         "evidence": []},
    ]},
    "flags": [], "booking_logs": [], "takedown": {"verdict": "No"},
    "resolution": "Full refund of EUR 84.",
    "suggested_response": "I'm so sorry your tickets never arrived. "
                          "We have refunded you in full.",
}


def _v4draft(**over):
    return _draft(rca_v3={**V4, **over.pop("rca_v3", {})}, **over)


def test_each_guest_issue_gets_its_own_block():
    """The old layout stacked five headings across ALL issues, so two
    complaints with different root causes were flattened into one list and the
    reader could not tell which cause belonged to which."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*1. Voucher never delivered*" in out
    assert "*2. Nobody answered the chat*" in out
    i, j = out.find("*1. Voucher"), out.find("*2. Nobody")
    assert "The fulfilment run failed silently." in out[i:j], \
        "issue 1's root cause is not inside issue 1's block"
    assert "First reply landed 40 minutes in." in out[j:], \
        "issue 2's root cause is not inside issue 2's block"


def test_the_verdict_and_owner_ride_the_issue_title():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*1. Voucher never delivered*  ·  Accurate  ·  RO" in out


def test_only_the_lines_an_issue_actually_has_are_printed():
    """A dash for every absent field turns a focused block into a form with
    blanks in it. Issue 2 has a fix and no sop_gap; issue 1 the reverse."""
    out = format_rca_slack(REVIEW, _v4draft())
    i, j = out.find("*1. Voucher"), out.find("*2. Nobody")
    assert "SOP gap:" in out[i:j] and "Fix:" not in out[i:j]
    assert "Fix:" in out[j:] and "SOP gap:" not in out[j:]


def test_evidence_keeps_its_source_and_reference():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "   - [bms] Three failed retries, no page. (ZD-4491)" in out


def test_the_guest_quote_survives():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "“I waited two hours and nothing came.”" in out


def test_an_issue_with_no_evidence_does_not_break_the_block():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*2. Nobody answered the chat*" in out


# ── the reply is never in the post ──────────────────────────────────────────

def test_the_guest_reply_never_reaches_the_slack_post():
    """The RCA thread is internal; the reply goes to Trustpilot by hand. A
    guest-facing apology in a leadership post is a field that would look
    entirely plausible in the output, which is why it is worth pinning."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "refunded you in full" not in out
    assert "so sorry" not in out.lower()
    assert "suggested_response" not in out


def test_the_resolution_is_still_posted():
    """Resolution is internal fact and belongs in the post — the exclusion is
    the guest-facing reply, not everything near it."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*Resolution*" in out


# ── facts and interpretation, merged by zd_ref ──────────────────────────────

FRAMES = [{"ticket_id": "4491", "time": "22 Jul 15:41", "thread": "chat",
           "guestSaid": "Where are my tickets?", "weDid": "Resent them."}]
NOTES  = [{"zd_ref": "ZD-4491", "summary": "Guest chased the voucher.",
           "ce_miss": "No proactive update after the first failure."}]


def test_the_rows_come_from_zendesk_and_the_gap_line_from_the_model():
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": NOTES})
    out = format_rca_slack(REVIEW, d)
    assert "22 Jul 15:41 · chat — Where are my tickets? | we: Resent them. (ZD-4491)" in out
    assert "⚠ CE miss: No proactive update after the first failure." in out


def test_the_model_cannot_restate_a_time_the_frame_already_has():
    """Facts stay with the pipeline. A note claiming a different time must not
    change the row — that is the precedence the split exists to protect."""
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes":
                         [{**NOTES[0], "time": "09:00", "channel": "call"}]})
    out = format_rca_slack(REVIEW, d)
    assert "22 Jul 15:41 · chat" in out
    assert "09:00" not in out


def test_a_contact_zendesk_does_not_have_still_renders_marked_unverified():
    """Either the guest reached us off Zendesk, or the model invented a
    contact. Silently dropping it hides both."""
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": NOTES + [
                     {"zd_ref": None, "channel": "call", "time": "22 Jul 14:10",
                      "summary": "Guest says they phoned and got no answer."}]})
    out = format_rca_slack(REVIEW, d)
    assert "Guest says they phoned and got no answer. (guest's account, unverified)" in out


def test_a_note_without_a_frame_is_not_double_counted():
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": NOTES})
    out = format_rca_slack(REVIEW, d)
    assert out.count("Guest chased the voucher") == 0, \
        "the note's summary is only a fallback when the frame has none"
    assert "unverified" not in out


def test_the_model_summary_fills_in_when_the_frame_has_none():
    d = _v4draft(support_interaction_frames=[{"ticket_id": "4491", "time": "22 Jul 15:41"}],
                 rca_v3={"support_interaction_notes": NOTES})
    assert "Guest chased the voucher." in format_rca_slack(REVIEW, d)


def test_sp_rows_are_frames_and_raised_comes_from_the_notes():
    d = _v4draft(sp_interaction_frames=[{"ticket_id": "7", "time": "22 Jul 16:02",
                                         "guestSaid": "Did the guide show?"}],
                 rca_v3={"sp_interaction_notes": {"raised": "Yes", "records": [
                     {"zd_ref": "ZD-7", "summary": "Operator confirmed the no-show."}]}})
    out = format_rca_slack(REVIEW, d)
    assert "raised with SP: Yes" in out
    assert "22 Jul 16:02 — Did the guide show? (ZD-7)" in out
