"""The cancellation policy is a booking detail, not a timeline row.

It was written onto EVERY row of the timeline — on one real ticket, all of
them — crowding out the fact each row existed to carry. It is a property of
the booking: it does not happen at a moment, and repeating it down a column
says nothing new each time.

Taking it off the timeline without putting it anywhere would have lost it.
There is no cancellation column in the warehouse and none in the API payload,
so the ticket text is the only place it exists, and it is extracted once.

"" IS NOT "THE BOOKING HAS NO POLICY". It means the ticket did not state it,
and the reason is carried alongside so the card can say which — a blank field
and "no ticket event states the terms" send a reader to different places.
"""
import pytest

from server.ticket_notes import cancellation_policy, policy_from_events


@pytest.mark.parametrize("text,want", [
    # The exact wording from the reported ticket.
    ("2 Adults; PLN 606.00; cancellable and reschedulable to 1440 min prior",
     "Cancellable up to 1440 minutes before start"),
    ("Booking confirmation; cancel/reschedule deadline 02 Aug 08:30; note",
     "Cancel or reschedule by 02 Aug 08:30"),
    ("non-cancellable / non-refundable", "Non-cancellable"),
    ("non cancellable", "Non-cancellable"),
    ("Free cancellation up to 24 hours before the tour",
     "Free cancellation up to 24 hours before start"),
])
def test_the_shapes_that_appear_on_real_tickets(text, want):
    assert cancellation_policy(text) == want


def test_a_body_that_says_nothing_about_terms_returns_empty():
    assert cancellation_policy("2 Adults; pickup Wielopole 2") == ""


def test_an_empty_body_does_not_raise():
    for v in ("", None, "   "):
        assert cancellation_policy(v) == ""


def test_non_cancellable_wins_over_a_deadline_elsewhere_in_the_text():
    """A booking that cannot be cancelled has no deadline, and printing one
    would tell a reader they can still act."""
    got = cancellation_policy(
        "non-cancellable; cancel/reschedule deadline 02 Aug 08:30")
    assert got == "Non-cancellable", got


# ── across the whole timeline ──────────────────────────────────────────────

def test_it_reads_every_event_not_just_the_booking_dump():
    """The terms turn up in confirmation emails too. Reading only the dump
    would report nothing when the answer was two rows away."""
    got, why = policy_from_events([
        {"raw_body": "Guest asked about the pickup point"},
        {"raw_body": "Confirmation sent; cancellable and reschedulable to "
                     "1440 min prior"}])
    assert got == "Cancellable up to 1440 minutes before start", (got, why)
    assert why == ""


def test_the_reason_says_the_tickets_were_silent():
    """Not the same as having no events at all, and the reader acts on the
    difference: one is a booking whose terms nobody wrote down, the other is a
    timeline that never loaded."""
    got, why = policy_from_events([{"raw_body": "a"}, {"raw_body": "b"}])
    assert got == ""
    assert "2 ticket event(s)" in why, why


def test_no_events_at_all_is_a_different_sentence():
    got, why = policy_from_events([])
    assert got == ""
    assert "no ticket events" in why, why
    assert why != policy_from_events([{"raw_body": "a"}])[1]


def test_a_summary_is_read_when_there_is_no_raw_body():
    """Older drafts stored the shaped summary and no raw body. Reading only
    raw_body would report nothing on every one of them."""
    got, _ = policy_from_events([{"summary": "non-cancellable"}])
    assert got == "Non-cancellable"


def test_malformed_events_do_not_raise():
    """It runs inside the save path, which must not die over a bad row."""
    assert policy_from_events([None, "not a dict", {}])[0] == ""


# ── it is carried, not recomputed on the card ──────────────────────────────

def test_the_pipeline_stores_both_the_policy_and_the_reason():
    """NEGATIVE source assertion, permitted by CLAUDE.md: the extractor can be
    perfect and change nothing if the save path drops it."""
    import inspect
    from server import pipeline
    src = inspect.getsource(pipeline.process_review)
    assert 'booking_to_save["cancellation_policy"]' in src, (
        "the cancellation policy is no longer carried onto the booking")
    assert 'cancellation_policy_note' in src, (
        "the reason there is no policy is no longer carried, so a blank field "
        "cannot say whether the ticket was silent")
