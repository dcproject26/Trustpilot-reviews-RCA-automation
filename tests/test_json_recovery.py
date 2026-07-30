"""A truncated model answer must degrade to a partial RCA, never to nothing.

The v3 answer is long - five WWR headings, booking logs, flags, two
interaction blocks, SOP compliance, the question bank, area-of-improving,
takedown, prevention. When it runs past the token limit the JSON arrives cut
mid-structure. Strict parsing turned that into an empty RCA panel with no
visible cause, on a case where everything else in the run had succeeded.
"""
import json

import pytest

from server.services.claude import _extract_json_object


FULL = {
    "tldr": {"our_mistake": "The page did not state the delivery window.",
             "our_fix": "Content is auditing the listing."},
    "what_went_wrong": {
        "guest_issues": [{"issue": "Tickets arrived late.", "claim_accuracy": "Partially True",
                          "evidence": ["[experience-page] No window stated.",
                                       "[zendesk] Mail promised two hours."]}],
        "what_happened": {"root_causes": [{"issue": "Delay", "cause": "Selenium ran late.",
                                           "classification": "Operational + HO"}],
                          "operational_failure": ["Chat unanswered for nine minutes."],
                          "sop_gap": ["No checkout warning."],
                          "pattern": "one-off - 0 similar in 90d"},
        "sp_escalation": {"escalated": "N/A", "detail": ["Vendor not partnered."]},
        "fixes": {"teams": ["Content"], "actions": ["Audit the page."],
                  "prevention": ["Add a callout."], "owner": "Content"},
    },
    "booking_logs": [{"time": "22 Jul 15:22", "what": "Email sent", "detail": "Promised 2h."},
                     {"time": "22 Jul 15:50", "what": "Tickets issued", "detail": ""}],
    "flags": [{"flag": "Window not on page.", "team": "content",
               "evidence": "redemption null", "zd_ref": "ZD-1"}],
    "support_interaction": [{"time": "15:41", "channel": "chat", "summary": "Asked for tickets.",
                             "ce_miss": None, "zd_ref": "ZD-2"}],
    "sp_interaction": {"possible": False, "reason_if_not": "Not partnered.",
                       "raised": "N/A", "detail": [], "zd_ref": ""},
    "sop_compliance": {"dss_available": True, "expected": "Resend.", "actual": "Refunded.",
                       "verdict": "followed", "detail": "Denial then HOC.", "zd_ref": "ZD-1"},
    "issue_specific_answers": {"Was the window disclosed?": "No ([experience-page] none)"},
    "area_of_improving": ["Surface the window at checkout."],
    "takedown": {"recommended": False, "reason": "Partially accurate."},
    "prevention": ["Add a checkout callout."],
}
TEXT = json.dumps(FULL, indent=2)


@pytest.mark.parametrize("cut", list(range(200, len(TEXT), 137)))
def test_every_truncation_point_recovers_something_valid(cut):
    """Cut the answer at many points; each must yield a valid dict carrying
    the fields that arrived before the cut."""
    got = _extract_json_object(TEXT[:cut])
    assert isinstance(got, dict) and got, f"nothing recovered from a {cut}-char answer"
    assert "tldr" in got, "the first field must always survive"
    json.dumps(got)   # must be serialisable, i.e. genuinely valid


def test_cut_inside_an_array_recovers():
    cut = TEXT.index('"[zendesk] Mail promised two hours."') + 12
    got = _extract_json_object(TEXT[:cut])
    assert got and got["what_went_wrong"]["guest_issues"], (
        "an answer cut inside a list is the common case - the long fields are lists")


def test_cut_mid_key_recovers():
    cut = TEXT.index('"booking_logs"') + 7   # mid-key
    got = _extract_json_object(TEXT[:cut])
    assert got and "what_went_wrong" in got


def test_intact_answer_is_returned_whole():
    assert _extract_json_object(TEXT) == FULL


def test_fenced_and_prefaced_answer_still_parses():
    assert _extract_json_object("Here you go:\n```json\n" + TEXT + "\n```") == FULL


def test_garbage_returns_none():
    assert _extract_json_object("no json here at all") is None
    assert _extract_json_object("") is None


def test_generate_rca_v3_recovers_a_truncated_answer(monkeypatch):
    """The RCA path itself must use the tolerant parser. This is the exact
    failure seen in production: every step of the run succeeded, the answer
    came back cut at the token limit, and the panel showed nothing."""
    import asyncio

    from server.services import claude as C

    async def _fake_call(prompt, max_tokens=0):
        return TEXT[: int(len(TEXT) * 0.7)]      # cut mid-structure

    monkeypatch.setattr(C, "_call", _fake_call)
    monkeypatch.setattr(C, "is_live", lambda name: True)

    out = asyncio.run(C.generate_rca_v3(
        review_text="r", booking={}, timeline=[], insights={}, dss_rec={},
        l1="L1", l2="L2", sub_theme="", support_summary="",
        checklist={"ce": [], "ro": [], "scenarios": {}}, review_id="tp_new"))

    assert out, "a truncated answer produced an EMPTY rca_v3 - the panel goes blank"
    assert out.get("tldr", {}).get("our_mistake"), "the fields before the cut must survive"


def test_generate_rca_v3_asks_for_enough_tokens(monkeypatch):
    """6000 was sized for the old shape and the v3 answer overruns it."""
    import asyncio

    from server.services import claude as C
    seen = {}

    async def _fake_call(prompt, max_tokens=0):
        seen["max_tokens"] = max_tokens
        return TEXT

    monkeypatch.setattr(C, "_call", _fake_call)
    monkeypatch.setattr(C, "is_live", lambda name: True)
    asyncio.run(C.generate_rca_v3(
        review_text="r", booking={}, timeline=[], insights={}, dss_rec={},
        l1="L1", l2="L2", sub_theme="", support_summary="",
        checklist={"ce": [], "ro": [], "scenarios": {}}, review_id="tp_new"))
    assert seen["max_tokens"] >= 12000, (
        f"asked for only {seen['max_tokens']} tokens; the v3 answer is long "
        f"enough that this truncates on real cases")
