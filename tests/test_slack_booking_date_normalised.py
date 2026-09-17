"""The Slack post showed 'Booking date: 1.789312429E9' — a raw epoch timestamp
from BigQuery that the client normalised but the Slack composer did not.

The fix is _normalise_stamp inside _booking_field, so every date-bearing row
in the Slack post goes through the same conversion the dashboard uses.
"""
from server.services.slack import _normalise_stamp, _booking_field


def test_an_epoch_in_scientific_notation_becomes_a_date():
    got = _normalise_stamp("1.789312429E9")
    assert got.startswith("2026-09-"), f"expected a Sept 2026 date, got {got!r}"
    assert "E9" not in got


def test_an_epoch_in_plain_digits_becomes_a_date():
    got = _normalise_stamp("1789312429")
    assert got.startswith("2026-09-"), got


def test_millisecond_epoch_is_handled():
    got = _normalise_stamp("1789312429000")
    assert got.startswith("2026-09-"), got


def test_an_iso_string_passes_through():
    assert _normalise_stamp("2026-09-14") == "2026-09-14"


def test_empty_value_passes_through():
    assert _normalise_stamp("") == ""
    assert _normalise_stamp(None) == ""


def test_booking_field_normalises_the_date():
    bk = {"date_of_booking": "1.789312429E9"}
    got = _booking_field(bk, ("date_of_booking",))
    assert "2026-09" in got
    assert "E9" not in got
