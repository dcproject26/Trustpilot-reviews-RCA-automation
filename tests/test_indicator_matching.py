"""A review naming a guest, a venue and a city must not end up untraceable
because the Zendesk ticket happens not to record an experience.

Manuel Enrique's review named the Colosseum, Rome, an approximate date and his
full name, and still landed in Untraceable. The venue check rejected any
ticket whose experience field was empty - treating an absent field as a
contradiction - so tickets whose requester name matched exactly were discarded
before anyone could see them.
"""
from server.services.zendesk import matches_indicators


IND = {"experience_or_venue": "Colosseum", "city_or_country": "Rome",
       "visit_date_hint": "2026-07-18"}


def test_ticket_without_an_experience_is_not_rejected():
    sig = {"guest_name": "Manuel Enrique", "booking_id": "1", "experience": ""}
    ok, used = matches_indicators(sig, IND, "Manuel", "Enrique")
    assert ok, "an empty experience field cannot contradict the review's venue"
    assert "name" in used
    assert "venue" not in used, "nothing was verified about the venue - do not claim it"


def test_ticket_with_a_matching_experience_still_matches():
    sig = {"guest_name": "Manuel Enrique", "booking_id": "1",
           "experience": "Colosseum Underground and Arena Floor Tour"}
    ok, used = matches_indicators(sig, IND, "Manuel", "Enrique")
    assert ok and "venue" in used


def test_ticket_with_a_contradicting_experience_is_still_rejected():
    sig = {"guest_name": "Manuel Enrique", "booking_id": "1",
           "experience": "Vatican Museums Skip-the-Line"}
    ok, _ = matches_indicators(sig, IND, "Manuel", "Enrique")
    assert not ok, "a present, disagreeing experience must still reject"


def test_wrong_name_is_still_rejected():
    sig = {"guest_name": "Someone Else", "booking_id": "1", "experience": ""}
    ok, _ = matches_indicators(sig, IND, "Manuel", "Enrique")
    assert not ok


def test_generic_venue_word_alone_does_not_match():
    sig = {"guest_name": "Manuel Enrique", "booking_id": "1",
           "experience": "Doge's Palace Tour"}
    ok, _ = matches_indicators(sig, {"experience_or_venue": "Palace of Culture"},
                               "Manuel", "Enrique")
    assert not ok, "overlapping only on a generic noun matches half the catalogue"


def test_city_absent_on_the_ticket_does_not_reject():
    sig = {"guest_name": "Manuel Enrique", "booking_id": "1",
           "experience": "", "city": ""}
    ok, _ = matches_indicators(sig, {"city_or_country": "Rome"}, "Manuel", "Enrique")
    assert ok
