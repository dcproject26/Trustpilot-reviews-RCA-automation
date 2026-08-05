"""Guest ↔ support holds conversations, and says what it moved out.

HANDOFF §4. Chat, call, email, web form and in-app messages are contacts.
Three things were being counted as contacts and are not:

    BOOKING — booking created        → the booking timeline (facts column)
    API     — posts, tickets sent, system and bot notes
                                     → the events timeline, marked internal
    REVIEW  — the review itself      → the events timeline's closing bookend

Each one counted raised the contact count, and a count that is too high reads
as a guest who was handled when nobody spoke to them.

The half that is easy to get wrong is the SILENCE. A filtered list and a guest
who never wrote in must not look the same — "0 contacts" over a booking with
four machine events and "0 contacts" over a booking with nothing at all are
opposite facts, so the split is returned as two lists and the count says how
many moved.

Driven through the functions Slack and the renderer actually call. The client
mirror of the same rule is asserted in the browser, in
tests/test_rca_ui_rendered.py.
"""
import pytest

from server.services.zendesk import (NON_CONTACT_THREADS, is_conversation,
                                     moved_frames_note, split_contact_frames)


def _fr(thread, **over):
    fr = {"thread": thread, "time": "22 Jul 15:41",
          "time_sort": "2026-07-22T15:41:00", "guestSaid": "hello"}
    fr.update(over)
    return fr


@pytest.mark.parametrize("thread", ["chat", "email", "call", "web", "app"])
def test_a_channel_a_person_used_is_a_contact(thread):
    assert is_conversation(_fr(thread)) is True


@pytest.mark.parametrize("thread", ["booking", "review", "api", "sp"])
def test_machinery_and_the_review_are_not_contacts(thread):
    assert is_conversation(_fr(thread)) is False


def test_internal_machinery_is_not_a_contact_whatever_thread_it_arrived_on():
    """The booking-in-progress mail is thread "email" and no human sent it.
    Filtering on the channel alone would keep it, and it is exactly the row
    that made an untouched booking look handled."""
    assert is_conversation(_fr("email", is_internal=True)) is False
    assert is_conversation(_fr("chat", is_internal=True)) is False


def test_an_unclassified_thread_stays_visible():
    """The denylist is the point. An allowlist would drop a channel nobody has
    classified yet — silently, on the reasoning that we cannot prove it is a
    conversation — and a real guest contact would vanish because Zendesk added
    a channel name. Wrong and visible beats gone."""
    assert is_conversation(_fr("carrier_pigeon")) is True
    assert is_conversation(_fr("")) is True


def test_the_split_returns_both_halves_not_one_list_with_the_rest_gone():
    frames = [_fr("booking"), _fr("chat"), _fr("api"), _fr("chat"),
              _fr("email", is_internal=True), _fr("review")]
    convo, moved = split_contact_frames(frames)
    assert len(convo) == 2, convo
    assert len(moved) == 4, moved
    assert len(convo) + len(moved) == len(frames), "a frame went missing"


def test_the_note_says_how_many_moved():
    _, moved = split_contact_frames([_fr("chat"), _fr("booking"), _fr("api"),
                                     _fr("review")])
    assert moved_frames_note(moved) == "3 system events moved to the timeline"


def test_one_moved_event_is_not_reported_as_plural():
    _, moved = split_contact_frames([_fr("chat"), _fr("booking")])
    assert moved_frames_note(moved) == "1 system event moved to the timeline"


def test_nothing_moved_says_nothing():
    """The inverse bug. "0 system events moved" on every clean booking is the
    noise that makes a reader stop reading the counts that do matter."""
    _, moved = split_contact_frames([_fr("chat"), _fr("email")])
    assert moved_frames_note(moved) == ""
    assert moved_frames_note([]) == ""
    assert moved_frames_note(None) == ""


def test_junk_in_the_frame_list_cannot_take_the_section_down():
    convo, moved = split_contact_frames([None, "not a frame", _fr("chat")])
    assert len(convo) == 1 and moved == []


def test_the_denylist_names_the_three_the_handoff_names():
    """A silent widening of this list is a silent emptying of the section."""
    assert set(NON_CONTACT_THREADS) == {"booking", "review", "api", "sp"}


# ── the Slack post reads the same rule ──────────────────────────────────────

def _draft(**over):
    from tests.test_slack_v3_format import _v4draft
    return _v4draft(**over)


def test_the_slack_post_counts_conversations_not_events():
    from server.services.slack import format_rca_slack
    from tests.test_slack_v3_format import REVIEW
    out = format_rca_slack(REVIEW, _draft(support_interaction_frames=[
        _fr("booking", guestSaid="Booking created"),
        _fr("chat", ticket_id="34011401", guestSaid="Where are my tickets?"),
        _fr("api", guestSaid="Booking details posted"),
        _fr("review", guestSaid="Review posted")]))
    assert "• 01." in out and "• 02." not in out, (
        "the booking, API and review rows are being counted as contacts")
    assert "3 system events moved to the timeline" in out


def test_a_booking_with_only_machinery_does_not_read_as_a_guest_who_never_wrote():
    from server.services.slack import format_rca_slack
    from tests.test_slack_v3_format import REVIEW
    out = format_rca_slack(REVIEW, _draft(
        rca_v3={"support_interaction_notes": []},
        support_interaction_frames=[_fr("booking"), _fr("api"), _fr("review")]))
    assert "No conversation with the guest" in out
    assert "3 system events moved to the timeline" in out
    assert "No guest contact found on this booking" not in out, (
        "a filtered list is reading as a guest who never reached us")


def test_a_booking_with_nothing_at_all_still_says_so_plainly():
    from server.services.slack import format_rca_slack
    from tests.test_slack_v3_format import REVIEW
    out = format_rca_slack(REVIEW, _draft(rca_v3={"support_interaction_notes": []},
                                          support_interaction_frames=[]))
    assert "No guest contact found on this booking" in out
    assert "moved to the timeline" not in out
