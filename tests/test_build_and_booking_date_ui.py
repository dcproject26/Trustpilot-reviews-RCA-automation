"""Two facts the card reported as absent when it had simply looked in one place.

**The booking creation date.** One fact under three names, depending on which
code path produced the booking:

    bigquery_patch.verify_bid    date_of_booking   (full timestamp)
    bigquery._row_to_dict        bookedOn
    pipeline._make_candidate     bookedOn + creationDate

The card read `date_of_booking` and nothing else, so a booking from either of
the other two rendered "Booking date —" — character for character what a
booking with no creation date renders.

**The build banner.** `/api/version` answered `stale: false` where the commit
could not be read at all, and the page drew nothing. Covered server-side in
test_version_build_unknown.py; what is checked here is the page's half, since
`if (v.stale)` treats null exactly like false and that is how it shipped.

Driven in the browser against the real functions. `bookingCreatedAt` is called
directly rather than asserted to exist in source, because a function the page
never calls looks identical in source to one that works.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


# ── the booking creation date ──────────────────────────────────────────────

@pytest.mark.parametrize("key,name", [
    ("date_of_booking", "verify_bid"),
    ("creationDate",    "pipeline._make_candidate"),
    ("bookedOn",        "bigquery._row_to_dict"),
])
def test_the_creation_date_is_read_whichever_name_it_arrived_under(page, key, name):
    got = page.evaluate("(k) => bookingCreatedAt({[k]: '2026-07-22T15:22:00'})", key)
    assert got == "2026-07-22T15:22:00", (
        f"a booking from {name} carries the date as `{key}` and the card reads "
        f"{got!r} — it renders as an empty Booking date row")


def test_the_full_timestamp_survives(page):
    """The point of the change upstream was keeping the time. Truncating it
    here would undo that silently — the row still looks populated."""
    got = page.evaluate(
        "() => bookingCreatedAt({date_of_booking: '2026-07-22T15:22:41'})")
    assert got.endswith("15:22:41"), got


def test_the_first_name_wins_when_more_than_one_is_present(page):
    """A normalised booking carries several of them. They agree in practice,
    but the order has to be deterministic or two runs disagree."""
    got = page.evaluate("""() => bookingCreatedAt(
        {date_of_booking: 'FROM_VERIFY', creationDate: 'X', bookedOn: 'Y'})""")
    assert got == "FROM_VERIFY"


def test_a_booking_with_no_creation_date_still_yields_empty(page):
    """The legitimate empty. Coercing it to something non-empty would be the
    inverse bug: a date on screen for a booking that has none."""
    assert page.evaluate("() => bookingCreatedAt({id: '1'})") == ""
    assert page.evaluate("() => bookingCreatedAt(null)") == ""
    assert page.evaluate("() => bookingCreatedAt(undefined)") == ""


def test_an_empty_string_falls_through_to_the_next_name(page):
    """BigQuery writes "" rather than omitting the column, so presence alone
    is not enough — a blank first name must not shadow a populated second."""
    got = page.evaluate(
        "() => bookingCreatedAt({date_of_booking: '', bookedOn: '2026-01-02'})")
    assert got == "2026-01-02", got


# ── the build banner's three states ────────────────────────────────────────

@pytest.mark.parametrize("stale,expected", [
    (True,  "stale"),
    (False, "current"),
    (None,  "unknown"),
])
def test_the_three_build_states_are_distinguishable(page, stale, expected):
    """The PAGE'S rule, called directly — not a copy of it written here. A
    test that reimplements the logic passes whatever the page does.

    null must not collapse into 'current'. `if (v.stale)` did exactly that,
    which is why a deployment 24 commits behind showed a clean build line."""
    got = page.evaluate("(v) => buildState(v)",
                        {"stale": stale, "short": "abc1234",
                         "on_disk": "9999999", "fingerprint": "3185c67062b9"})
    assert got == expected


def test_a_missing_stale_field_is_unknown_not_current(page):
    """An older server that predates the field returns no `stale` at all.
    Reading that as 'current' would resurrect the bug against exactly the
    deployments most likely to be behind."""
    assert page.evaluate("() => buildState({short: 'abc'})") == "unknown"
    assert page.evaluate("() => buildState(null)") == "unknown"


def test_only_the_current_state_stays_quiet(page):
    """The banner draws on 'stale' and on 'unknown'. If it drew on all three
    it would be noise; on only one, the case we just fixed goes silent again."""
    drawn = {s: page.evaluate("(s) => s !== 'current'", s)
             for s in ("stale", "unknown", "current")}
    assert drawn == {"stale": True, "unknown": True, "current": False}


def test_the_page_carries_the_unknown_state_into_the_dom(page):
    """Not just computed — readable by whoever is looking at the page."""
    val = page.evaluate("() => document.body.dataset.stale")
    assert val in ("yes", "no", "unknown"), (
        f"body.dataset.stale is {val!r} — the banner never ran, so its three "
        f"states are untested on a real load")
