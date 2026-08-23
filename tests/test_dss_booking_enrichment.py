"""DSS filter inputs must reach every match path.

isPartnered (the cancelation partnered filter) and amountUSD (the value note)
come from _get_booking_extra. Several match paths set `booking` straight from
verify_bid, which carries neither — the direct-BID path (a booking id in the
review or an attachment, the commonest match) among them. DSS then ran with
is_partnered unknown and the value note empty on those reviews.

_needs_booking_extra decides, at the point every path converges, whether the
booking still needs enriching — keyed on the PRESENCE of isPartnered, so a
path that already asked (even to an unknown answer) is not re-queried and a
path that never asked is.
"""
from server.pipeline import _needs_booking_extra


def test_a_verify_bid_booking_needs_enrichment():
    # verify_bid's shape: id + dates + experience, no isPartnered / amountUSD.
    booking = {"id": "33587369", "date_of_booking": "2026-08-21",
               "experienceName": "Alcatraz Day Tour"}
    assert _needs_booking_extra(booking, candidate_state=False) is True


def test_a_present_but_unknown_ispartnered_is_not_re_queried():
    # A path already ran _get_booking_extra and the vendor join said nothing:
    # isPartnered is None. That is "asked, unknown", NOT "never asked" — the key
    # is present, so it is not queried again.
    booking = {"id": "1", "isPartnered": None}
    assert _needs_booking_extra(booking, candidate_state=False) is False


def test_an_enriched_booking_is_not_re_queried():
    booking = {"id": "1", "isPartnered": True, "amountUSD": 300.0}
    assert _needs_booking_extra(booking, candidate_state=False) is False


def test_a_candidate_is_not_enriched():
    # Candidates are not a single matched booking; DSS does not run on them.
    assert _needs_booking_extra({"id": "1"}, candidate_state=True) is False


def test_a_booking_with_no_id_is_not_enriched():
    assert _needs_booking_extra({"date_of_booking": "2026-08-21"},
                                candidate_state=False) is False


def test_no_booking_is_not_enriched():
    assert _needs_booking_extra({}, candidate_state=False) is False
    assert _needs_booking_extra(None, candidate_state=False) is False
