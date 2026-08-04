"""The booking creation event carries a time, not just a date.

"the booking creation date in both events timeline and booking logs does not
have the timestamp, only date"

It was not a display bug. `_VERIFY_BID_SQL` selected `DATE(b.created_at)`, so
the time was discarded at the warehouse and nothing downstream ever received
it — the model could not put it in `booking_logs`, the timeline could not sort
on it, and the formatter was carrying the blame for truncating something it
was never given.

Every other event in the timeline is 'DD Mon HH:MM IST'. One bare date in the
middle of them reads as a missing value rather than as a different precision,
which is the reason it was noticed at all.
"""
import re

import pytest

from server.prompts import _fmt_bookend_time, _fmt_date_ist


# ── the query keeps the time ────────────────────────────────────────────────

def test_the_booking_query_selects_the_timestamp():
    """A negative assertion — DATE() around created_at appears nowhere. The
    one thing a source check is good for: a string that is absent cannot be
    absent for the wrong reason."""
    sql = open("server/services/bigquery_patch.py", encoding="utf-8").read()
    assert "DATE(b.created_at)" not in sql, (
        "the booking timestamp is being truncated at the warehouse again; "
        "nothing downstream can recover it")
    assert "b.created_at            AS date_of_booking" in sql


def test_the_visit_date_is_still_a_date():
    """The other half. An experience is booked FOR a day — giving it a
    spurious 00:00 would read as a time somebody recorded."""
    sql = open("server/services/bigquery_patch.py", encoding="utf-8").read()
    assert "DATE(b.experience_date) AS date_of_visit" in sql


# ── the formatter renders it ────────────────────────────────────────────────

@pytest.mark.parametrize("raw", [
    "2026-07-22T15:22:00Z",
    "2026-07-22 15:22:00+00:00",
    "2026-07-22T15:22:00",
])
def test_a_timestamp_renders_with_its_time(raw):
    got = _fmt_date_ist(raw)
    assert re.match(r"^\d{2} \w{3} \d{2}:\d{2} IST$", got), got


def test_a_date_only_value_still_renders_as_a_date():
    """Older bookings, and anything that genuinely has no time. It must not
    grow a fake 00:00."""
    got = _fmt_date_ist("2026-07-22")
    assert got == "22 Jul 2026"
    assert "00:00" not in got


def test_the_two_are_not_rendered_the_same():
    assert _fmt_date_ist("2026-07-22T15:22:00Z") != _fmt_date_ist("2026-07-22")


def test_the_bookend_matches_the_shape_of_a_real_event():
    """The booking-created bookend sits among Zendesk events formatted
    'DD Mon HH:MM IST'. A different shape sorts and reads as a special case,
    which is what the client used to hand-patch around."""
    got = _fmt_bookend_time("2026-07-22T15:22:00Z")
    assert re.match(r"^\d{2} \w{3} \d{2}:\d{2} IST$", got), got


# ── the prompt hands the model the time ─────────────────────────────────────

def test_the_prompt_gives_the_model_a_timestamped_booking_date():
    """If the token is a bare date the model cannot put a time in booking_logs
    however the rule is worded."""
    from server.prompts import _bookend
    got = _bookend({"date_of_booking": "2026-07-22T15:22:00Z"}, "date_of_booking",
                   "creationDate")
    assert "15:22" in got or "20:52" in got, got     # UTC or IST


def test_a_missing_booking_date_is_named_rather_than_invented():
    from server.prompts import _bookend
    got = _bookend({}, "date_of_booking", "creationDate")
    assert "not recorded" in got
    assert "2026" not in got, "a date was invented for a booking that has none"
