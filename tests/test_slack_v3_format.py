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


def test_the_sections_still_frame_the_post():
    """The section structure and the include-picker keys, minus the two that
    were removed from the RCA."""
    out = format_rca_slack(REVIEW, _draft())
    for must in ("*What went wrong*", "1. 22 Jul 15:22 — Booking-in-progress email sent",
                 "2. 22 Jul 15:50 — Tickets issued",
                 "*Booking logs*", "*Flags*",
                 "*Review takedown*", "*Experience insights*"):
        assert must in out, f"missing: {must!r}"


def test_the_removed_sections_are_not_posted():
    """A heading with nothing under it is worse in Slack than on the card —
    it goes to the whole team and reads as a section that failed to fill."""
    out = format_rca_slack(REVIEW, _draft())
    assert "*TL;DR*" not in out
    assert "*SOP compliance*" not in out


def test_pointer_lists_never_print_as_python_reprs():
    """The document-level pointer fields are LISTS, and some entries are
    `{point: ...}` dicts. Joining the raw value puts a Python repr into a
    Slack post — the same class of defect as the stringified fix object.

    Evidence rows are no longer in the post at all (the five-heading mandate),
    so the fields this checks are the ones that still reach it: the
    operational failure and SOP gap folded in from `what_happened`.
    """
    out = format_rca_slack(REVIEW, _draft())
    assert "The chat went unanswered for nine minutes." in out
    assert "No checkout warning exists for same-day Selenium bookings." in out
    assert "{'point'" not in out
    assert "['" not in out


def test_evidence_rows_are_not_in_the_post_at_all():
    """They stay on the dashboard. Checked here as well as in the v4 block
    tests because this is the LEGACY draft path, which had its own renderer."""
    out = format_rca_slack(REVIEW, _draft())
    assert "[experience-page] No window is stated." not in out
    assert "[zendesk] The email promised two hours." not in out


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


def test_each_guest_issue_repeats_the_whole_five_heading_structure():
    """The user mandated five headings; several complaints REPEAT them rather
    than listing under 1a. Listing under 1a is the flattening this project
    already fixed once — two complaints with different root causes went into
    one list and the reader could not tell which cause belonged to which."""
    from server.services.wwr_post import headings
    out = format_rca_slack(REVIEW, _v4draft())
    for h in headings():
        assert out.count(h) == 2, f"{h} should appear once per guest issue"


def test_each_issues_root_cause_stays_inside_its_own_block():
    out = format_rca_slack(REVIEW, _v4draft())
    i, j = out.find("Voucher never delivered"), out.find("Nobody answered the chat")
    assert i != -1 and j != -1 and i < j
    assert "The fulfilment run failed silently." in out[i:j], \
        "issue 1's root cause is not inside issue 1's block"
    assert "First reply landed 40 minutes in." in out[j:], \
        "issue 2's root cause is not inside issue 2's block"


def test_the_blocks_are_labelled_so_they_can_be_told_apart():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*Guest issue 1 of 2*" in out
    assert "*Guest issue 2 of 2*" in out


def test_the_verdict_prints_in_the_users_vocabulary():
    """Yes / Partially True / No — not the model's four-value enum. The card
    still shows `claim_accuracy` verbatim; the POST speaks the user's words."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "2. Is the guest's claim accurate? Yes" in out
    assert "2. Is the guest's claim accurate? Partially True" in out
    assert "Partly accurate" not in out


def test_the_owner_chip_no_longer_rides_the_issue_title():
    """Per-issue owner chips came OUT of the post. The owner is named under
    heading 5, which is where the mandate puts the team to tag."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "*1. Voucher never delivered*  \u00b7  Accurate  \u00b7  RO" not in out


def test_only_the_subpoints_an_issue_actually_has_are_printed():
    """Sub-points a/b/c are INDICATIVE. Issue 1 has an SOP gap and issue 2
    does not, and a dash for every absent field turns a focused block into a
    form with blanks in it."""
    out = format_rca_slack(REVIEW, _v4draft())
    i, j = out.find("Voucher never delivered"), out.find("Nobody answered the chat")
    assert "SOP/process gap:" in out[i:j]
    assert "SOP/process gap:" not in out[j:]


def test_evidence_rows_come_out_of_the_post():
    """Evidence stays on the dashboard. The post carries the five headings and
    nothing else."""
    out = format_rca_slack(REVIEW, _v4draft())
    assert "Three failed retries, no page." not in out
    assert "ZD-4491" not in out


def test_the_guest_quote_comes_out_of_the_post():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "\u201cI waited two hours and nothing came.\u201d" not in out
    assert "I messaged twice and got nothing." not in out


def test_the_accuracy_note_comes_out_of_the_post():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "The fulfilment log confirms it." not in out


def test_an_issue_with_no_evidence_does_not_break_the_block():
    out = format_rca_slack(REVIEW, _v4draft())
    assert "Nobody answered the chat" in out
    assert "First reply landed 40 minutes in." in out


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


def test_the_contact_is_the_row_and_its_events_sit_under_it():
    """The Events timeline is the per-event view; this is the per-contact one.
    One row per frame would make the contact count report events."""
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": NOTES})
    out = format_rca_slack(REVIEW, d)
    assert "• 01. 22 Jul 15:41 · chat — Guest chased the voucher. (ZD-4491)" in out
    assert "   - 22 Jul 15:41 — Where are my tickets? | we: Resent them." in out
    assert "⚠ CE miss: No proactive update after the first failure." in out


def test_several_events_on_one_ticket_are_one_contact():
    """Three messages on one ticket is one contact, not three."""
    frames = [dict(FRAMES[0], time=f"22 Jul 15:4{i}", guestSaid=f"msg {i}")
              for i in range(3)]
    out = format_rca_slack(REVIEW, _v4draft(support_interaction_frames=frames))
    assert out.count("• 01.") == 1
    assert "• 02." not in out
    assert "[3 events]" in out
    for i in range(3):
        assert f"msg {i}" in out, "an event was swallowed by the grouping"


def test_untickted_frames_minutes_apart_are_one_contact():
    """Without the time-window fallback each untracked message becomes its own
    contact — the same inflation by another route."""
    frames = [{"time": "22 Jul 15:41", "time_sort": "2026-07-22T15:41:00",
               "guestSaid": "first"},
              {"time": "22 Jul 15:49", "time_sort": "2026-07-22T15:49:00",
               "guestSaid": "second"}]
    out = format_rca_slack(REVIEW, _v4draft(support_interaction_frames=frames))
    assert out.count("• 01.") == 1 and "• 02." not in out


def test_untickted_frames_hours_apart_are_separate_contacts():
    frames = [{"time": "22 Jul 09:00", "time_sort": "2026-07-22T09:00:00",
               "guestSaid": "morning"},
              {"time": "22 Jul 18:00", "time_sort": "2026-07-22T18:00:00",
               "guestSaid": "evening"}]
    out = format_rca_slack(REVIEW, _v4draft(support_interaction_frames=frames))
    assert "• 01." in out and "• 02." in out


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


def test_a_joined_note_is_not_also_rendered_as_an_orphan():
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": NOTES})
    out = format_rca_slack(REVIEW, d)
    assert out.count("Guest chased the voucher") == 1
    assert "unverified" not in out and "unmatched" not in out


def test_the_frame_summary_fills_in_when_the_model_has_no_note():
    out = format_rca_slack(REVIEW, _v4draft(support_interaction_frames=FRAMES))
    assert "• 01. 22 Jul 15:41 · chat — Where are my tickets? (ZD-4491)" in out


def test_a_note_whose_reference_matched_nothing_says_so():
    """A silent zero is the failure mode: a join that matches nothing looks
    exactly like a model that returned no notes. An orphan carrying a zd_ref is
    a failed join, not an off-Zendesk contact, and reads differently."""
    d = _v4draft(support_interaction_frames=FRAMES,
                 rca_v3={"support_interaction_notes": [
                     {"zd_ref": "ZD-9999", "summary": "A contact on no known ticket."}]})
    out = format_rca_slack(REVIEW, d)
    assert "A contact on no known ticket. (ZD-9999) (unmatched ZD reference)" in out


def test_sp_rows_are_frames_and_raised_comes_from_the_notes():
    d = _v4draft(sp_interaction_frames=[{"ticket_id": "7", "time": "22 Jul 16:02",
                                         "guestSaid": "Did the guide show?"}],
                 rca_v3={"sp_interaction_notes": {"raised": "Yes", "records": [
                     {"zd_ref": "ZD-7", "summary": "Operator confirmed the no-show."}]}})
    out = format_rca_slack(REVIEW, d)
    assert "raised with SP: Yes" in out
    assert "22 Jul 16:02 — Did the guide show? (ZD-7)" in out
