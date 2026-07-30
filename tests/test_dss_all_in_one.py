"""DSS All-in-One parity tests.

The lookup must apply the Retool app's exact hard filters (social media
always Yes, is_Partenered, value > $125) and only then score selectors.
Fixtures monkeypatch the tab fetch, so these run anywhere - no network.
"""
import asyncio

import pytest

from server.services import dss


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


def _rec(**kw):
    return asyncio.run(dss.get_recommendation(
        kw.pop("booking", {}), "rev_x", **kw))


def test_cancelation_filters_partnered_and_value():
    r = _rec(booking={"isPartnered": False, "amountUSD": 300.0},
             l1="Miscellaneous Issue", l2="Guest cancellation request",
             review_text="had to cancel for health issues")
    assert r["dss_type"] == "cancelation"
    assert r["action"] == "unpartnered-high-value policy"
    assert r["filters"]["value_greater_125"] == "yes"
    assert r["filters"]["is_partnered"] == "no"


def test_social_media_row_never_wins():
    # Same query but the only difference between two candidate rows is the
    # social flag - the non-social row must lose even when value matches.
    r = _rec(booking={"isPartnered": False, "amountUSD": 50.0},
             l1="Miscellaneous Issue", l2="Cancellation",
             review_text="cancel because of health issues")
    assert r["action"] == "unpartnered-low-value policy"
    assert "NON-social" not in r["action"]


def test_unknown_inputs_do_not_filter():
    # No isPartnered, no amount: both cancelation variants stay candidates,
    # and the result must say the inputs were unknown, not pick a side.
    r = _rec(booking={}, l1="Miscellaneous Issue", l2="Cancellation",
             review_text="cancel for health issues please")
    assert r["match_score"] > 0
    assert r["filters"]["is_partnered"] == "unknown"
    assert r["filters"]["value_greater_125"] == "unknown"


def test_meeting_point_routes_by_keyword():
    r = _rec(booking={}, l1="Operations Issue", l2="Meeting point confusion",
             review_text="nobody at the meeting point, guide not present")
    assert r["dss_type"] == "meetingPointIssue"
    assert r["action"] == "guide-not-present policy"


def test_sp_routes_by_l1():
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


def test_delay_fulfilment_value_fork():
    low = _rec(booking={"amountUSD": 80.0}, l1="Operations Issue",
               l2="Tickets not received",
               review_text="tickets never received before the visit")
    high = _rec(booking={"amountUSD": 200.0}, l1="Operations Issue",
                l2="Tickets not received",
                review_text="tickets never received before the visit")
    assert low["dss_type"] == high["dss_type"] == "delay_fulfilment"
    assert low["action"] == "delay-low-value policy"
    assert high["action"] == "delay-high-value policy"


def test_no_match_returns_fallback():
    r = _rec(booking={}, l1="Business Issue", l2="Pricing complaint",
             review_text="totally unrelated topic zzz")
    assert r["match_score"] == 0
    assert r["fallback"] == dss.NO_DSS_MESSAGE


def test_mock_mode_untouched(monkeypatch):
    monkeypatch.setattr(dss, "is_live", lambda name: False)
    assert asyncio.run(dss.get_recommendation({}, "rev_unknown")) == {}
