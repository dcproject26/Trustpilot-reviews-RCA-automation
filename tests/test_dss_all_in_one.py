"""DSS All-in-One parity tests.

The lookup applies the Retool app's hard filters (social media always Yes,
is_Partenered for the cancelation tab, and never the Escalations desk's rows),
then an AI selector reads the review and picks the scenario that means the same
thing. A deterministic keyword scorer is the FALLBACK when the model is
unavailable.

Two deliberate changes from the earlier contract, both by request:
  - value > $125 is NO LONGER a hard gate. Booking value is passed to the
    selector as context and surfaced to the associate as `value_note`; it no
    longer appears in `filters` and no longer forks the chosen row.
  - L2 no longer routes or gates. Routing on L2 blanked DSS before the review
    was ever read (the L2=None -> out_of_scope -> no DSS cascade). The type is
    decided by the selector reading the review; `out_of_scope` is never set.

Fixtures monkeypatch the tab fetch, so these run anywhere - no network. With no
model key in the sandbox the real select_dss_scenario raises and the keyword
fallback runs (selector == "keyword-fallback"); tests that need the AI path
monkeypatch claude.select_dss_scenario directly.
"""
import asyncio

import pytest

from server.services import claude, dss


CANCEL_ROWS = [
    # (reason, partnered, social, value>125, dss text)
    # The non-social row sits FIRST: score ties break by order, so if the
    # social-media filter is ever dropped, this row wins and a test fails -
    # last in the list it would lose the tie and mask the regression.
    {"cancelation_reason": "Health issues", "is_partenered": "No",
     "for_social_media": "No", "is_value_greater": "No",
     "dss": "NON-social-media policy - must never be chosen"},
    {"cancelation_reason": "Health issues", "is_partenered": "Yes",
     "for_social_media": "Yes", "is_value_greater": "No",
     "dss": "partnered-low-value policy"},
    {"cancelation_reason": "Health issues", "is_partenered": "No",
     "for_social_media": "Yes", "is_value_greater": "No",
     "dss": "unpartnered-low-value policy"},
    {"cancelation_reason": "Health issues", "is_partenered": "No",
     "for_social_media": "Yes", "is_value_greater": "Yes",
     "dss": "unpartnered-high-value policy"},
]

MP_ROWS = [
    {"scenarios": "Guide not present at MP",
     "when_did_the_guest_reached_out": "After visit",
     "dss": "guide-not-present policy"},
    {"scenarios": "Cx running late",
     "when_did_the_guest_reached_out": "Before visit",
     "dss": "running-late policy"},
]

SP_ROWS = [
    # Escalations variants FIRST for the same tie-break reason as above:
    # if the CE/RO-only filter is dropped, one of these wins and a test fails.
    {"scenarios": "Venue was closed - ES",
     "dss": "ESCALATIONS venue policy - must never be chosen"},
    {"scenarios": "Venue was closed.", "team": "Escalations",
     "dss": "ESCALATIONS-team venue policy - must never be chosen"},
    {"scenarios": "Venue was closed.", "team": "RO_CE",
     "dss": "venue-closed policy"},
    {"scenarios": "Tickets are not accepted at the venue.",
     "dss": "tickets-not-accepted policy"},
]

DELAY_ROWS = [
    {"delay_fulfilment_reason": "Tickets not received",
     "is_value_greater": "No", "dss": "delay-low-value policy"},
    {"delay_fulfilment_reason": "Tickets not received",
     "is_value_greater": "Yes", "dss": "delay-high-value policy"},
]

ALL_TABS = {
    "cancelation":        CANCEL_ROWS,
    "meetingPointIssue":  MP_ROWS,
    "supplyPartnerIssue": SP_ROWS,
    "delay_fulfilment":   DELAY_ROWS,
}


@pytest.fixture(autouse=True)
def _live_with_fixture_tabs(monkeypatch):
    monkeypatch.setattr(dss, "is_live", lambda name: True)

    async def fake_tabs():
        return ALL_TABS
    monkeypatch.setattr(dss, "_get_tabs", fake_tabs)


@pytest.fixture(autouse=True)
def _no_unified_export(monkeypatch):
    """This file tests the LIVE SHEET's filters and keyword routing, against
    stub rows built for that purpose.

    The checked-in unified export supersedes a live row wherever the two name
    the same scenario, which is the point of it — but it means these results
    would depend on whether the real export happens to cover each stub's
    scenario. It covers the meeting-point one and not the cancellation one, so
    the file passed in parts for a reason that has nothing to do with what it
    is testing.

    Superseding has its own tests, in tests/test_dss_unified.py, where the
    live rows and the export rows are both controlled.
    """
    monkeypatch.setattr(dss, "_UNIFIED", {})


def _rec(**kw):
    return asyncio.run(dss.get_recommendation(
        kw.pop("booking", {}), "rev_x", **kw))


def test_cancelation_partnered_filter_still_applies():
    # value > 125 is no longer a hard gate, but the partnered filter for the
    # cancelation tab still is: an unpartnered booking must never be handed a
    # partnered-only row, and never the non-social row. With no model key the
    # keyword fallback runs, so this also pins that the fallback actually ran -
    # a blanked selector would read the same as "nothing matched".
    r = _rec(booking={"isPartnered": False, "amountUSD": 300.0},
             l1="Miscellaneous Issue", l2="Guest cancellation request",
             review_text="had to cancel for health issues")
    assert r["dss_type"] == "cancelation"
    assert r["action"].startswith("unpartnered")       # not partnered, not NON-social
    assert r["action"] != "partnered-low-value policy"
    assert r["filters"]["is_partnered"] == "no"
    assert "value_greater_125" not in r["filters"]      # value is no longer a gate
    assert r["selector"] == "keyword-fallback"
    assert "300" in r["value_note"]                     # value present -> note set


def test_social_media_row_never_wins():
    # Same query but the only difference between two candidate rows is the
    # social flag - the non-social row must lose even when value matches.
    r = _rec(booking={"isPartnered": False, "amountUSD": 50.0},
             l1="Miscellaneous Issue", l2="Cancellation",
             review_text="cancel because of health issues")
    assert r["action"] == "unpartnered-low-value policy"
    assert "NON-social" not in r["action"]


def test_unknown_inputs_do_not_filter():
    # No isPartnered, no amount: the cancelation variants stay candidates, and
    # the result must say the partnered input was unknown, not pick a side.
    # value is no longer reported as a filter at all, and with no amount there
    # is no value note.
    r = _rec(booking={}, l1="Miscellaneous Issue", l2="Cancellation",
             review_text="cancel for health issues please")
    assert r["match_score"] > 0
    assert r["filters"]["is_partnered"] == "unknown"
    assert "value_greater_125" not in r["filters"]
    assert r["filters"]["amount_usd"] is None
    assert r["value_note"] == ""


def test_meeting_point_routes_by_keyword():
    r = _rec(booking={}, l1="Operations Issue", l2="Meeting point confusion",
             review_text="nobody at the meeting point, guide not present")
    assert r["dss_type"] == "meetingPointIssue"
    assert r["action"] == "guide-not-present policy"


def test_sp_venue_closed_matches_ce_ro_row():
    # L1 no longer routes; the keyword fallback matches the venue scenario and
    # the Escalations variants are filtered out, so the CE/RO row wins.
    r = _rec(booking={}, l1="Venue Related Issue", l2="Venue closed",
             review_text="the venue was closed when we arrived")
    assert r["dss_type"] == "supplyPartnerIssue"
    assert r["action"] == "venue-closed policy"


def test_escalations_rows_never_win():
    # Same scenario exists as "- ES" selector variant AND as an
    # Escalations-team row - both must lose to the CE/RO row even though
    # they sit first in the tab and score identically.
    r = _rec(booking={}, l1="Venue Related Issue", l2="Venue closed",
             review_text="venue was closed")
    assert "ESCALATIONS" not in r["action"]
    assert r["coverage"] == "CE/RO"


def test_value_is_no_longer_a_hard_fork():
    # The value fork is gone: with no model, a low- and a high-value booking
    # pick the SAME delay row (keyword tie -> first). The value shows up only as
    # a note, not as a filter that silently changed the row.
    low = _rec(booking={"amountUSD": 80.0}, l1="Operations Issue",
               l2="Tickets not received",
               review_text="tickets never received before the visit")
    high = _rec(booking={"amountUSD": 200.0}, l1="Operations Issue",
                l2="Tickets not received",
                review_text="tickets never received before the visit")
    assert low["dss_type"] == high["dss_type"] == "delay_fulfilment"
    assert low["action"] == high["action"]              # value no longer forks the row
    assert "value_greater_125" not in low["filters"]
    assert "80" in low["value_note"] and "200" in high["value_note"]
    assert low["selector"] == "keyword-fallback"


def test_no_match_returns_fallback():
    r = _rec(booking={}, l1="Business Issue", l2="Pricing complaint",
             review_text="totally unrelated topic zzz")
    assert r["match_score"] == 0
    assert r["fallback"] == dss.NO_DSS_MESSAGE


def test_mock_mode_untouched(monkeypatch):
    monkeypatch.setattr(dss, "is_live", lambda name: False)
    assert asyncio.run(dss.get_recommendation({}, "rev_unknown")) == {}


def test_an_uncovered_l2_no_longer_blanks_dss():
    """Regression for the manual-review cascade: routing on L2 blanked DSS
    before the review was ever read (L2=None -> out_of_scope -> no DSS). L2 no
    longer gates - the selector reads the review and can still find a scenario,
    and out_of_scope is never set."""
    r = _rec(booking={}, l1="Operations Issue",
             l2="Content - Instructions not clear / Misleading Info",
             review_text="tickets never received, we waited and nothing came")
    assert r.get("out_of_scope") is None
    assert r["dss_type"] == "delay_fulfilment"
    assert r["match_score"] > 0


def test_a_delay_review_still_reaches_delay_fulfilment():
    """A ticket-delay review reaches delay_fulfilment through the keyword
    fallback even when the L2 label is a generic 'Ticket Issues'."""
    r = _rec(booking={"amountUSD": 50.0}, l1="Operations Issue",
             l2="Ticket Issues",
             review_text="tickets never received before the visit")
    assert r["dss_type"] == "delay_fulfilment"
    assert not r.get("out_of_scope")


# ── AI-selector path ─────────────────────────────────────────────────────────
# In the sandbox the real select_dss_scenario raises (no model key) and the
# keyword fallback runs. These monkeypatch it to drive the AI path itself: a
# pick, an explicit no-match, a model outage, and the filter/AI ordering.

def _ai(monkeypatch, fn):
    async def fake(**kw):
        return fn(kw)
    monkeypatch.setattr(claude, "select_dss_scenario", fake)


def test_the_unified_other_tab_does_not_break_the_lookup(monkeypatch):
    # Regression: the checked-in unified export carries an 'other' tab, which is
    # not one of the fixed four in TABS. Building candidates across all tabs then
    # did TABS['other'] and raised KeyError; the pipeline swallowed it and DSS
    # returned nothing for EVERY review. The row must instead be a normal,
    # selectable candidate scoring on 'scenarios'.
    monkeypatch.setattr(dss, "_UNIFIED", {"other": [
        {"_unified": True, "scenarios": "audio guide app would not open",
         "dss": "OTHER-tab guidance"}]})
    r = _rec(booking={}, l1="Operations Issue", l2="App issue",
             review_text="the audio guide app would not open at all")
    assert r["dss_type"] == "other"          # reached, not KeyError-swallowed
    assert r["action"] == "OTHER-tab guidance"
    assert r["selector"] == "keyword-fallback"


def test_ai_selector_picks_the_matching_scenario(monkeypatch):
    # AI is handed only the FILTERED candidates and picks by meaning. We locate
    # the guide-not-present row by scanning the payload, so the assertion does
    # not depend on candidate ordering.
    def choose(kw):
        for c in kw["candidates"]:
            if c["scenario"] == "Guide not present at MP":
                return {"index": c["i"], "confidence": "high",
                        "reason": "guide was absent at the meeting point"}
        return {"index": -1, "confidence": "low", "reason": "none"}
    _ai(monkeypatch, choose)
    r = _rec(booking={}, l1="Operations Issue", l2="anything",
             review_text="the guide simply never showed up")
    assert r["selector"] == "ai"
    assert r["dss_type"] == "meetingPointIssue"
    assert r["action"] == "guide-not-present policy"
    assert r["match_score"] == 5
    assert r["selector_reason"] == "guide was absent at the meeting point"


def test_ai_selector_saying_none_is_a_real_no_match(monkeypatch):
    # index -1 means the model read the scenarios and judged none fits - a
    # genuine no-match, marked distinctly from a model outage.
    _ai(monkeypatch, lambda kw: {"index": -1, "confidence": "high",
                                 "reason": "no scenario fits this review"})
    r = _rec(booking={}, l1="X", l2="Y", review_text="something with no policy")
    assert r["selector"] == "ai-none"
    assert r["match_score"] == 0
    assert r["dss_type"] == ""
    assert r["fallback"] == dss.NO_DSS_MESSAGE
    assert r["selector_reason"] == "no scenario fits this review"


def test_ai_outage_falls_back_to_keyword_not_to_no_dss(monkeypatch):
    # A model error must degrade to the deterministic keyword scorer, never to a
    # false "No DSS available" - that is the whole reason the scorer is kept.
    async def boom(**kw):
        raise RuntimeError("model down")
    monkeypatch.setattr(claude, "select_dss_scenario", boom)
    r = _rec(booking={}, l1="Operations Issue", l2="Meeting point confusion",
             review_text="nobody at the meeting point, guide not present")
    assert r["selector"] == "keyword-fallback"
    assert r["dss_type"] == "meetingPointIssue"
    assert r["action"] == "guide-not-present policy"


def test_ai_never_sees_a_partnered_row_for_an_unpartnered_booking(monkeypatch):
    # The hard filters run BEFORE the AI sees candidates: an unpartnered
    # booking's payload must not carry the partnered-only or non-social
    # cancelation rows.
    seen = {}
    def capture(kw):
        seen["actions"] = [c["action"] for c in kw["candidates"]]
        return {"index": -1, "confidence": "low", "reason": "n/a"}
    _ai(monkeypatch, capture)
    _rec(booking={"isPartnered": False}, l1="Miscellaneous Issue",
         l2="Cancellation", review_text="cancel for health issues")
    assert seen.get("actions"), "AI selector was never called"
    assert "partnered-low-value policy" not in seen["actions"]
    assert "NON-social-media policy - must never be chosen" not in seen["actions"]
