"""One composer for the what-went-wrong section, proved by driving both ends.

The dashboard and `services/slack.py` used to build this section
independently — the same block of text written twice, in two languages. That
is how "Fix: [object Object]" reached a real Slack post from the client while
the server's version was correct: no server-side test could see it, because
the server was not the thing that was wrong.

The fix is that there is now exactly one composer, and the client renders its
output verbatim off `draft.wwr_slack_text`. These tests drive the real paths —
`format_rca_slack()` and `_draft_dict()` — and assert the text they carry is
the SAME STRING. Agreement is then a property of the code rather than of two
implementations being kept in step by hand.
"""
from types import SimpleNamespace

import pytest

from server.api import _draft_dict
from server.services.slack import format_rca_slack
from server.services.wwr_post import compose


REVIEW = SimpleNamespace(id="tp_1", rating=2, author="Sam", language="EN",
                         received_at=None, insights={})

V4 = {
    "what_went_wrong": {
        "guest_issues": [
            {"issue": "Tickets never arrived",
             "claim": "I waited all day and nothing came",
             "claim_accuracy": "Accurate",
             "claim_accuracy_note": "no delivery attempt is on the booking",
             "root_cause": "The vendor API timed out and nothing retried",
             "operational_failure": "No agent picked up the failed job",
             "sop_gap": "There is no retry SOP for vendor timeouts",
             "pattern": "second case this week",
             "evidence": [{"source": "zendesk", "text": "guest chased twice",
                           "ref": "ZD-99"}],
             "fix": {"action": "Add a retry with an alert",
                     "owner": "PRODUCT",
                     "because": "nothing retried the failed job"}},
        ],
        "sp_escalation": {"escalated": "No", "reason_if_not": "SP is on DND"},
        "fixes": {"teams": ["PRODUCT"], "actions": ["Add a retry"]},
    },
    "flags": [],
    "booking_logs": [],
}


def _draft(**over):
    base = dict(
        id="draft_tp_1", review_id="tp_1",
        booking={"id": "32908218"}, rca_v3=V4,
        l1="Operations Issue", l2="Ticket Issues", sub_theme="",
        primary_scenario="Tickets sent late", overlay_scenarios=[],
        wwr_scenarios=[], wwr_chain=[], support_interaction_frames=[],
        sp_interaction_frames=[], area_of_improving=[], actions_taken={},
        resolution="", checklist_answers=[], insights={},
        confidence_trail=[], candidates_list=[], timeline=[], timeline_raw=[],
        similar_support=[], similar_reviews=[], dss_rec={},
        zendesk_ticket_ids=[], diagnostic_checks=[],
        what_went_wrong_bullets=[], sub_themes=[], scenarios=[],
        issue_specific_answers=[], evidence=[], prevention=[],
        slack_thread_override="", slack_mentions=[], ticket_facts={},
        support_summary="", l1_reasoning="", stated_issue="",
        suggested_response="", final_response="", template_name="",
        response_english=None, response_english_of=None,
        match_tier=1, match_confidence="high", match_method="ref",
        candidate_state=False, selected_candidate_bid=None,
        bid_source="ref", extracted_signals={}, narrowing_attempts=[],
        flag_to_biz_state="", flag_to_biz_message="",
        generated_at=None, rca_posted_at=None, rca_v3_edited_at=None,
        sent_at=None, dss_connected_at=None, rca_prompt_version="v4",
        sop_compliance={}, booking_logs=[], flags=[], takedown={}, dss={},
        guest_issues=[], review=REVIEW, rca_fields={}, signals=[],
    )
    base.update(over)
    return SimpleNamespace(**base)


# ── The agreement guarantee ─────────────────────────────────────────────────

def test_the_draft_carries_the_same_text_the_post_does():
    """THE guarantee. The client renders `wwr_slack_text` verbatim, so if that
    string is what the posted RCA contains, the preview and the post cannot
    disagree about this section — not because two implementations match, but
    because there is only one."""
    d = _draft()
    served = _draft_dict(d)["wwr_slack_text"]
    posted = format_rca_slack(REVIEW, d)
    assert served, "the draft served no what-went-wrong text at all"
    assert served in posted, (
        "the text the dashboard renders is not the text that gets posted:\n"
        f"--- served ---\n{served}\n--- posted ---\n{posted}")


def test_both_ends_come_from_the_one_composer():
    d = _draft()
    direct = compose(V4["what_went_wrong"])
    assert _draft_dict(d)["wwr_slack_text"] == direct
    assert direct in format_rca_slack(REVIEW, d)


def test_they_still_agree_after_the_rca_is_edited():
    """An inline edit changes rca_v3, and the client re-reads
    `wwr_slack_text` off the PATCH response. If an edit could move one and not
    the other, the preview would go stale silently — which is the whole class
    of defect this replaced."""
    edited = {"what_went_wrong": {
        "guest_issues": [{"issue": "Refund refused",
                          "claim_accuracy": "Inaccurate",
                          "root_cause": "The SOP was applied correctly"}],
        "sp_escalation": {"escalated": "N/A"}}}
    d = _draft(rca_v3=edited)
    served = _draft_dict(d)["wwr_slack_text"]
    assert "Refund refused" in served
    assert "Tickets never arrived" not in served
    assert served in format_rca_slack(REVIEW, d)


def test_the_fix_object_is_never_stringified_on_either_end():
    d = _draft()
    served = _draft_dict(d)["wwr_slack_text"]
    posted = format_rca_slack(REVIEW, d)
    for blob in (served, posted):
        assert "[object Object]" not in blob
        assert "{'action'" not in blob


# ── What the post drops ─────────────────────────────────────────────────────

def test_the_posted_section_drops_the_evidence_and_the_quote():
    """Evidence rows, the verbatim guest quote, `pattern` and the accuracy
    note stay on the dashboard. They are removed from the POST only."""
    served = _draft_dict(_draft())["wwr_slack_text"]
    assert "I waited all day" not in served
    assert "guest chased twice" not in served
    assert "ZD-99" not in served
    assert "second case this week" not in served
    assert "no delivery attempt is on the booking" not in served


def test_the_dashboard_still_has_the_dropped_fields():
    """Removed from the post is not removed from the card. The v3 blob the
    dashboard renders still carries every one of them, so this is a change to
    one renderer and not a data loss."""
    served = _draft_dict(_draft())
    gi = served["rca_v3"]["what_went_wrong"]["guest_issues"][0]
    assert gi["claim"] == "I waited all day and nothing came"
    assert gi["pattern"] == "second case this week"
    assert gi["evidence"][0]["ref"] == "ZD-99"
    assert gi["claim_accuracy_note"]


def test_the_five_headings_survive_the_round_trip_to_the_post():
    from server.services.wwr_post import headings
    posted = format_rca_slack(REVIEW, _draft())
    for h in headings():
        assert h in posted


# ── Empty and broken are distinguishable at the draft boundary ──────────────

def test_a_draft_with_no_wwr_and_no_legacy_serves_an_empty_string():
    d = _draft(rca_v3={"flags": []}, wwr_scenarios=[], wwr_chain=[])
    assert _draft_dict(d)["wwr_slack_text"] == ""


def test_a_legacy_draft_serves_the_five_headings_rather_than_nothing():
    """A pre-v4 draft has no what_went_wrong node. Serving "" would make the
    dashboard drop the section entirely — indistinguishable from a composer
    that broke."""
    d = _draft(rca_v3={},
               wwr_scenarios=[{"scenario_name": "Late entry",
                               "accuracy": "Accurate", "why": "queue mismanaged",
                               "fix": "add a queue SOP", "is_primary": True}])
    served = _draft_dict(d)["wwr_slack_text"]
    assert "queue mismanaged" in served
    from server.services.wwr_post import headings
    for h in headings():
        assert h in served


def test_a_composer_failure_names_the_section_rather_than_vanishing():
    """A malformed node must not take the whole card down, and must not
    silently render as an RCA with nothing to say. Driven through the real
    wrapper `_draft_dict` calls, with the failure inside the composer's own
    reach — an exception raised earlier in the draft projection is a different
    fault and is not this one's to catch."""
    from server.api import _wwr_slack_text

    class Exploding(dict):
        def get(self, *a, **k):
            raise RuntimeError("boom")

    served = _wwr_slack_text(_draft(), Exploding(what_went_wrong={}))
    assert "could not be composed" in served
    assert "boom" in served


def test_an_empty_section_is_not_reported_as_a_composer_failure():
    """The inverse bug, and just as bad: a healthy draft with nothing to say
    must not be dressed up as an error."""
    from server.api import _wwr_slack_text
    assert _wwr_slack_text(_draft(wwr_scenarios=[], wwr_chain=[]), {}) == ""
