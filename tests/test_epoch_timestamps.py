"""A machine number where a date belongs.

The timeline's "Booking created" row rendered as `1.785752592E9`. BigQuery
returns a TIMESTAMP as epoch seconds, and a float that large str()s into
scientific notation. The formatter recognised neither, fell through its
`except`, and returned the input unchanged — so a 13-character number sat in
the timeline where the reader expects a time, and read as a broken row rather
than as a booking created on 3 Aug.

Two rules here, and the second matters as much as the first:

  * epoch seconds and milliseconds both parse;
  * a number that is NOT a plausible timestamp says so, rather than being
    printed as though it were one. "unreadable timestamp" is a fact. "42" in
    a time column is a puzzle.

Bounded by a date window rather than by digit count. Seconds and milliseconds
are both 10-13 digits at different points in history, and reading one as the
other is fifty years of error with nothing on screen to show for it.
"""
import pytest

from server.prompts import _fmt_date_ist, _fmt_bookend_time

# 1785752592 = 2026-08-03 10:23:12 UTC = 15:53 IST. The same instant the
# Zendesk events on that booking carry, which is what made it noticeable.
EPOCH = 1785752592
EXPECT = "03 Aug 15:53 IST"


@pytest.mark.parametrize("value", [
    "1.785752592E9",      # what BigQuery's float actually str()s to
    "1.785752592e9",
    EPOCH,
    str(EPOCH),
    float(EPOCH),
    EPOCH * 1000,         # milliseconds
    str(EPOCH * 1000),
])
def test_an_epoch_renders_as_a_time(value):
    assert _fmt_date_ist(value) == EXPECT, (
        f"{value!r} rendered as {_fmt_date_ist(value)!r} — a machine number "
        f"in a time column reads as a broken row")


def test_the_bookend_carries_it_too():
    """The timeline's first row is built by _fmt_bookend_time, and that is
    where it was seen."""
    assert _fmt_bookend_time("1.785752592E9") == EXPECT


@pytest.mark.parametrize("value", ["42", "0", "-1", "999", "1e15"])
def test_a_number_that_is_not_a_timestamp_says_so(value):
    """Printing it unchanged is what put 1.78E9 on the card. An unreadable
    value has to be reported as unreadable, not rendered as though it were
    the answer."""
    assert _fmt_date_ist(value) == "unreadable timestamp", value


# ── everything that already worked still works ─────────────────────────────

def test_an_iso_datetime_is_unaffected():
    assert _fmt_date_ist("2026-08-02T15:22:41") == "02 Aug 20:52 IST"


def test_a_date_only_value_keeps_its_year():
    assert _fmt_date_ist("2026-08-02") == "02 Aug 2026"


def test_the_bookend_degrades_a_date_only_value_to_day_and_month():
    """Real events carry no year, so a bookend that showed one sorted and read
    as a special case."""
    assert _fmt_bookend_time("2026-08-02") == "02 Aug"


def test_an_empty_value_is_unknown_not_a_guess():
    assert _fmt_date_ist("") == "unknown"
    assert _fmt_date_ist(None) == "unknown"


def test_a_non_numeric_string_is_left_alone():
    """Only NUMBERS get the timestamp treatment. A text value that is not a
    date is a different failure and must not be relabelled as one."""
    assert _fmt_date_ist("not a date") == "not a date"
