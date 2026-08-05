""""How we built this match" is the match, and nothing else.

The section carried every step the pipeline logged, so a reader asking "is
this the right booking?" was reading:

    Reply voice — written against 5 approved macro(s) for Operations Issue /
    Content - Instructions not clear, as tone only, closest: "Vendor Service
    Issue - (Guide/host not clear)", from the checked-in macros…
    RCA — 2 event(s) have no ticket id and were grouped into contacts by a
    30-minute window

Neither can bear on whether the booking is right. The identifiers — the BID,
the venue, the date, the Zendesk searches — were buried among them.

Driven in the browser against the page's own function. Filtering on the bold
label rather than a phase field because every appender already writes one;
an UNLABELLED step is kept, since dropping what we cannot classify would lose
a match step for the sake of tidiness.
"""
import pytest

pytest.importorskip("playwright.sync_api")

from tests.test_rca_ui_rendered import page, CHROME          # noqa: E402,F401


def _keep(page, label, rest="- something happened"):
    return page.evaluate(
        "(s) => matchTrail([{mark: 'pass', text: s}]).length === 1",
        f"<strong>{label}</strong> {rest}")


@pytest.mark.parametrize("label", [
    "BID extracted", "BQ verify", "Tier 1 confirmed", "Author parsed",
    "Zendesk returned too many results", "No booking matches these indicators",
    "Dates line up", "Booking fetched", "No name to search",
])
def test_a_match_step_is_kept(page, label):
    assert _keep(page, label) is True, (
        f"{label!r} was dropped from the match trail — it is how the match "
        f"was built and is the one thing this section must not lose")


@pytest.mark.parametrize("label", [
    "RCA", "Reply voice", "Draft", "DSS", "Insights", "Classification",
    "Macros", "Slack post", "Sub-themes", "Checklist",
    # Reported twice from the dashboard, and the reason it survived is that
    # the filter anchored on the first word: this label leads with "The". The
    # FULL label, not a prefix — a test asserting on "The reply" would have
    # passed against a regex that only ever saw the first two words.
    "The reply is an approved macro, sent as written",
])
def test_a_later_stage_step_is_not_in_the_match_trail(page, label):
    assert _keep(page, label) is False, (
        f"{label!r} is still in 'How we built this match' — it cannot bear on "
        f"whether this is the right booking")


def test_an_unlabelled_step_is_kept(page):
    """We cannot classify it, so we do not drop it. A silent drop here loses
    an identifier; a kept extra line costs one line."""
    assert page.evaluate(
        "() => matchTrail([{mark:'pass', text:'no bold label here'}]).length") == 1


def test_the_filter_does_not_empty_a_real_trail(page):
    """The guard on the guard. A regex that matched everything would leave the
    section blank, which reads as a match that was never attempted."""
    n = page.evaluate("""() => matchTrail([
      {mark:'pass', text:'<strong>BID extracted</strong> via regex: 33118844'},
      {mark:'pass', text:'<strong>BQ verify:</strong> confirmed via venue, date'},
      {mark:'warn', text:'<strong>RCA</strong> — grouped by a 30-minute window'},
      {mark:'pass', text:'<strong>Reply voice</strong> — 5 approved macros'}
    ]).length""")
    assert n == 2, f"{n} steps kept — expected the two match steps"


def test_an_empty_trail_stays_empty(page):
    assert page.evaluate("() => matchTrail([]).length") == 0
    assert page.evaluate("() => matchTrail(null).length") == 0
