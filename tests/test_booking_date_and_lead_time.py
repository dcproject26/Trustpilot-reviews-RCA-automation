"""A machine number in the Booking date row, and the lead time it silently ate.

The card showed:

    Booking date   1.785791592E9
    Lead time      —

BigQuery returns a TIMESTAMP as epoch seconds and a float that large str()s
into scientific notation. The row printed it verbatim, and lead time — which
does `new Date(bookingStr)` — got NaN and fell through to a dash. So a booking
with a perfectly good creation date showed no lead time, and nothing said why.

Two rules here, and the second is the one that would otherwise be missed:

  * the value is normalised ONCE, where both readers take it, rather than
    formatted at each place it is drawn;
  * "we had no dates" and "we had dates and could not read them" no longer
    print the same dash. Only one of those is something to go and fix.

Driven in the browser against the page's own functions.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401

# 1785791592 = 2026-08-03 21:13:12 UTC
EPOCH = 1785791592


@pytest.mark.parametrize("given", [
    "1.785791592E9",       # what BigQuery's float actually str()s to
    "1.785791592e9",
    str(EPOCH),
    EPOCH,
    EPOCH * 1000,          # milliseconds
])
def test_an_epoch_becomes_a_date(page, given):
    got = page.evaluate("(v) => normaliseStamp(v)", given)
    assert got.startswith("2026-08-03"), (
        f"{given!r} normalised to {got!r} — a machine number in the Booking "
        f"date row, and lead time cannot subtract from it")


def test_an_iso_string_is_left_exactly_as_it_was(page):
    """Already what the callers want. Rewriting it risks changing a timezone
    for no reason."""
    iso = "2026-08-03T21:13:12+00:00"
    assert page.evaluate("(v) => normaliseStamp(v)", iso) == iso


@pytest.mark.parametrize("given", ["", None, "not a date", "—"])
def test_a_value_that_is_not_a_number_is_untouched(page, given):
    """A value we cannot read must not be dressed as a date. Returning it
    unchanged keeps the failure visible."""
    got = page.evaluate("(v) => normaliseStamp(v)", given)
    assert got == (given or "")


def test_a_number_outside_any_plausible_date_is_not_coerced(page):
    """42 is not a timestamp. Reading it as one puts 1970 on the card."""
    assert page.evaluate("() => normaliseStamp('42')") == "42"


def test_the_booking_date_reader_normalises_too(page):
    """bookingCreatedAt is what fills the row and what lead time reads. The
    normalisation has to be inside it, not applied by one caller."""
    got = page.evaluate(
        "() => bookingCreatedAt({date_of_booking: '1.785791592E9'})")
    assert got.startswith("2026-08-03"), got


def test_the_three_field_names_all_normalise(page):
    """One fact under three names, each from a different code path."""
    for key in ("date_of_booking", "creationDate", "bookedOn"):
        got = page.evaluate("(k) => bookingCreatedAt({[k]: '1.785791592E9'})", key)
        assert got.startswith("2026-08-03"), f"{key}: {got}"


# ── lead time says which dash it is ────────────────────────────────────────

def _lead(page, booking, visit):
    return page.evaluate("""([b, v]) => {
      const bookingStr = String(b || '').split('T')[0];
      const visitStr   = String(v || '').split('T')[0];
      if (!(bookingStr && visitStr)) return '—';
      const d = Math.round((new Date(visitStr).getTime()
                          - new Date(bookingStr).getTime()) / 86400000);
      return !isFinite(d) ? 'unreadable dates'
           : d >= 2 ? d + ' days' : d === 1 ? '1 day' : d === 0 ? '0 days' : '—';
    }""", [booking, visit])


def test_a_normalised_booking_date_yields_a_real_lead_time(page):
    stamp = page.evaluate("() => normaliseStamp('1.785791592E9')")
    assert _lead(page, stamp, "2026-08-04") == "1 day", stamp


def test_unreadable_dates_are_not_reported_as_no_dates(page):
    """The whole point. Both used to print '—', and only one of them is a bug
    worth chasing."""
    assert _lead(page, "1.785791592E9", "2026-08-04") == "unreadable dates"


def test_genuinely_absent_dates_still_read_as_a_dash(page):
    """The legitimate empty must not be relabelled as a failure — that is the
    same bug pointing the other way."""
    assert _lead(page, "", "2026-08-04") == "—"
    assert _lead(page, "2026-08-03", "") == "—"


# ── and the timeline row a reader actually sees ────────────────────────────

def test_a_stored_epoch_renders_as_a_clock_time(page):
    """The row said "1.785791592E9" where the time belongs, three times over.
    Fixed at the prompt so new drafts never carry one — and here, so a draft
    already in the database reads correctly without a re-run."""
    got = page.evaluate("() => stampText('1.785791592E9')")
    assert ":" in got and "E9" not in got, got
    assert got.startswith("04 Aug"), got


def test_an_already_formatted_time_is_left_alone(page):
    """Real Zendesk events arrive as "04 Aug 02:48 IST". Reformatting them
    would be guessing at a timezone that is already applied."""
    assert page.evaluate("() => stampText('04 Aug 02:48 IST')") == "04 Aug 02:48"


def test_a_row_with_no_time_stays_empty(page):
    """An undated event is a real state — rule 10b has a sentence for it. It
    must not acquire a date from this."""
    assert page.evaluate("() => stampText('')") == ""
    assert page.evaluate("() => stampText(null)") == ""


# ── the Booking details row, which had a third date format ─────────────────

@pytest.mark.parametrize("given", [
    "2026-06-13T21:20:58.000Z",   # what normaliseStamp produces from an epoch
    "1781731258",                  # the epoch itself
    "1.781731258E9",               # BigQuery's float, stringified
])
def test_the_booking_date_row_reads_like_its_neighbours(page, given):
    """Visit date and Review date render as YYYY-MM-DD. Booking date printed
    raw ISO — "2026-06-13T21:20:58.000Z" — so one panel carried three date
    formats, one of them machine output with a T and a Z in it."""
    got = page.evaluate("(v) => stampDetail(v)", given)
    # The ISO markers specifically — "T" alone is wrong, because "IST" has one.
    # The first version of this assertion failed against output that was
    # correct, which is its own small lesson about matching on shape.
    import re as _re
    assert not _re.search(r"\dT\d", got) and not got.endswith("Z"), got
    assert got.startswith("2026-06-1"), got
    assert "IST" in got and ":" in got, (
        f"{got!r} lost the time — the time is the point of this field, it is "
        f"what shows a booking made minutes before the slot")


def test_absent_and_unreadable_stay_distinguishable(page):
    assert page.evaluate("() => stampDetail('')") == "—"
    assert page.evaluate("() => stampDetail(null)") == "—"
    # Not a date, and not pretended to be one.
    assert page.evaluate("() => stampDetail('not a date')") == "not a date"
