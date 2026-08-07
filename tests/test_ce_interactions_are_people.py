"""A booking dump rendered as something the guest said.

THE ROW, from a real Slack post under "Customer / CE interactions":

    03 Aug 15:53 IST — General Admission, 1-day pass; 2 Adults, 2 Children,
    2 Seniors; EUR 203.35 paid; no add-ons selected | we: —

That is a booking-detail event. It reached the guest-contact list because
`is_conversation` calls itself "an exchange a PERSON took part in" and then
tested only the thread and the internal flag:

    return thread not in NON_CONTACT_THREADS and not frame.get("is_internal")

`_map_channel` returns "email" for any via.channel it does not recognise, so
the row's thread was not in NON_CONTACT_THREADS, and nothing had marked it
internal. Both tests passed. Nobody asked who took part — though
`_detect_actor` had already answered: "system".

The filter RAN and matched nothing, which is why the section looked healthy.
"""
from server.services.zendesk import (is_conversation, split_contact_frames,
                                     moved_frames_note)


def _f(**kw):
    base = {"thread": "email", "actor": "guest", "is_internal": False,
            "guestSaid": "Where are my tickets?"}
    base.update(kw)
    return base


THE_ROW = _f(actor="system", guestSaid="General Admission, 1-day pass; "
             "2 Adults, 2 Children, 2 Seniors; EUR 203.35 paid", weDid="")


# ── the reported row ────────────────────────────────────────────────────────

def test_the_booking_dump_is_not_a_conversation():
    assert is_conversation(THE_ROW) is False


def test_it_passes_both_of_the_old_tests():
    """The fixture has to still exercise the defect: a row excluded by thread
    or by is_internal would prove nothing about the actor check."""
    assert THE_ROW["thread"] not in ("booking", "review", "api", "sp")
    assert not THE_ROW["is_internal"]


def test_a_real_guest_message_on_the_same_thread_survives():
    """The channel is identical. Only the actor differs, which is the whole
    point — filtering by thread could never have separated these two."""
    assert is_conversation(_f(actor="guest")) is True


# ── who counts as a person ──────────────────────────────────────────────────

def test_the_people_are_kept():
    for who in ("guest", "co", "sp", "ai"):
        assert is_conversation(_f(actor=who)) is True, who


def test_the_machinery_is_not():
    for who in ("system", "creation", "review"):
        assert is_conversation(_f(actor=who)) is False, who


def test_a_frame_with_no_actor_is_left_alone():
    """Frames written before the actor was recorded carry none. Reading a
    missing field as machinery would empty this section for every one of them
    — the inverse bug, and a worse one: it turns a guest who was handled into
    a guest nobody spoke to."""
    assert is_conversation(_f(actor="")) is True
    f = _f()
    del f["actor"]
    assert is_conversation(f) is True


def test_an_unknown_actor_is_not_assumed_to_be_a_person():
    """A new actor name must not silently join the conversation list. It is
    excluded and COUNTED, so it surfaces as a number rather than as a booking
    dump in a Slack post."""
    assert is_conversation(_f(actor="integration_bot")) is False


# ── nothing is dropped in silence ───────────────────────────────────────────

def test_what_is_excluded_is_counted_and_said():
    """Over-filtering fails safe ONLY because of this. A wrongly excluded
    contact shows up as a count that does not match; a wrongly included one is
    a booking dump presented to the team as something the guest said."""
    convos, moved = split_contact_frames([_f(actor="guest"), THE_ROW,
                                          _f(actor="system")])
    assert len(convos) == 1
    assert len(moved) == 2
    assert "2 system events moved" in moved_frames_note(moved)


def test_a_clean_booking_says_nothing():
    """"0 system events moved" on every healthy booking is the noise that
    makes a reader stop reading the counts that matter."""
    convos, moved = split_contact_frames([_f(actor="guest")])
    assert len(convos) == 1 and moved_frames_note(moved) == ""


def test_a_booking_nobody_contacted_reads_as_that_and_not_as_empty():
    """All machinery: the section must say the guest was not spoken to, which
    is a different fact from a section that lost its contents."""
    convos, moved = split_contact_frames([THE_ROW])
    assert convos == []
    assert "1 system event moved" in moved_frames_note(moved)
