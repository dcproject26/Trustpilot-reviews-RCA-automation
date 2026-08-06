"""The review date must be when the guest POSTED it, not when we saw it.

The card said "Review date 2026-08-05" and the timeline said "05 Aug — Review
posted" for a review that was not posted on the 5th. Both were rendering the
Slack message timestamp: when Trustpilot's integration relayed the review into
the channel, which is minutes after publication on a good day and hours after
it on a bad one.

THREE DIFFERENT FACTS WERE SHARING ONE COLUMN:

  1. the Trustpilot publish time — what "Review date" claims to be;
  2. the Slack arrival time — when the integration got round to it;
  3. the ingest moment — when our batch happened to run, which is not a fact
     about the review at all.

They rendered identically, so a reader comparing the review against a ticket
raised the same morning was reading a gap that could be wrong by the entire
relay delay, with nothing on screen to say so. CLAUDE.md §1.

WHAT THIS DOES NOT DO: guess. A date-shaped string in an untitled attachment
field is ignored — a visit date, a booking date and a refund date are
indistinguishable to a regex, and quietly picking one would turn a missing
date into a confidently wrong one, which is strictly worse.
"""
from datetime import datetime

import pytest

from server.services.slack import review_published_at, parse_review
from server.api import _received_at_from

# 2026-08-02 09:15:00 UTC — the review's real publication moment.
PUBLISHED = 1785662100.0
# 2026-08-05 — three days later, when the relay finally posted it.
ARRIVED = "1785931800.0"


def _ev(**kw):
    base = {"ts": ARRIVED, "channel": "C1", "text": "", "blocks": [],
            "attachments": []}
    base.update(kw)
    return base


# ── where the publish date is read from ────────────────────────────────────

def test_the_attachment_timestamp_is_used():
    """Slack's own attachment `ts`. An integration that sets it is stating
    when the thing happened, not when it sent the message."""
    got, src = review_published_at(_ev(attachments=[{"ts": PUBLISHED}]))
    assert got.date() == datetime(2026, 8, 2).date(), got
    assert src == "attachment_ts"


@pytest.mark.parametrize("value", [
    "2 August 2026", "2 Aug 2026", "August 2, 2026", "Aug 2 2026", "2026-08-02",
])
def test_a_dated_field_is_read_in_the_shapes_trustpilot_writes(value):
    got, src = review_published_at(_ev(attachments=[
        {"fields": [{"title": "Review date", "value": value}]}]))
    assert got.date() == datetime(2026, 8, 2).date(), (value, got)
    assert src.startswith("field:")


def test_the_footer_is_read_when_there_is_no_field():
    """Trustpilot writes the date in footer prose alongside the stars."""
    got, src = review_published_at(_ev(attachments=[
        {"footer": "★✩✩✩✩ Not verified · Reviewed on 2 August 2026"}]))
    assert got.date() == datetime(2026, 8, 2).date(), got
    assert src == "footer"


def test_the_attachment_timestamp_beats_a_footer_date():
    """Ordered by how directly each asserts the fact. An explicit epoch is a
    stronger claim than a date parsed out of prose."""
    _, src = review_published_at(_ev(attachments=[
        {"ts": PUBLISHED, "footer": "Reviewed on 9 September 2026"}]))
    assert src == "attachment_ts"


# ── and where it is deliberately NOT read from ─────────────────────────────

def test_an_untitled_date_shaped_field_is_ignored():
    """The whole reason this is title-driven. A visit date in an unlabelled
    field looks exactly like a publish date, and using it would put a
    confidently wrong date where an honest gap belongs."""
    got, src = review_published_at(_ev(attachments=[
        {"fields": [{"title": "", "value": "2 August 2026"}]}]))
    assert got is None, got
    assert src == ""


def test_a_visit_date_field_is_not_mistaken_for_a_publish_date():
    got, _ = review_published_at(_ev(attachments=[
        {"fields": [{"title": "Visit date", "value": "2 August 2026"}]}]))
    assert got is None, got


def test_an_empty_payload_says_it_has_nothing_rather_than_returning_a_date():
    """The empty source is what lets the caller tell "no publish date in the
    payload" from "the publish date is the epoch"."""
    got, src = review_published_at(_ev())
    assert got is None
    assert src == ""


def test_a_nonsense_timestamp_is_rejected_rather_than_becoming_1970():
    """0, "", and 1.7e9-as-milliseconds all parse as floats. A date in 1970
    on the card is a wrong answer wearing the clothes of a right one."""
    for bad in (0, "", "not a ts", 17859318000000, -5):
        got, _ = review_published_at(_ev(attachments=[{"ts": bad}]))
        assert got is None, bad


# ── what the ingest does with it ───────────────────────────────────────────

def test_the_publish_date_wins_over_the_slack_arrival_time():
    """The reported bug, end to end. Published on the 2nd, relayed on the 5th,
    and the card said the 5th."""
    got = _received_at_from(ARRIVED, "rv1",
                            datetime(2026, 8, 2, 9, 15), "attachment_ts")
    assert got.date() == datetime(2026, 8, 2).date(), got


def test_the_arrival_time_is_still_used_when_the_payload_has_no_date():
    """A legitimate fallback, not a failure — and it must not become a
    blank column. Trustpilot does not always send the date, and an empty
    "Review date" is less useful than a late one."""
    got = _received_at_from(ARRIVED, "rv1", None, "")
    assert got.date() == datetime(2026, 8, 5).date(), got


def test_a_broken_slack_ts_still_falls_back_to_now_rather_than_crashing():
    """The last resort. Stamped with the ingest moment, which is not a fact
    about the review — hence the log line."""
    got = _received_at_from("not-a-timestamp", "rv1", None, "")
    assert isinstance(got, datetime)


# ── and that parse_review carries it ───────────────────────────────────────

def test_parse_review_returns_the_publish_date_and_its_source():
    """Wiring. The extractor can be perfect and change nothing if the parser
    drops it before the ingest sees it."""
    out = parse_review(_ev(text="★✩✩✩✩ terrible",
                           attachments=[{"ts": PUBLISHED}]))
    assert out["published_at"].date() == datetime(2026, 8, 2).date()
    assert out["published_at_source"] == "attachment_ts"


def test_parse_review_reports_an_absent_date_as_absent():
    out = parse_review(_ev(text="★✩✩✩✩ terrible"))
    assert out["published_at"] is None
    assert out["published_at_source"] == ""
