"""Which internal ticket notes reach the timeline.

Internal notes were hidden wholesale behind a toggle, which was wrong in both
directions: a reschedule that FAILED is the case itself and was buried, while
"please close this once the guest confirms" is noise that says nothing about
the booking.

THE TEST IS WHAT THE NOTE IS FOR, NOT WHAT IT MENTIONS — and the note that
forced that wording is "NAR, tix are already rescheduled for +45 mins". It is
a disposition instruction (No Action Required) wrapped around a real outcome,
and that outcome is the only record we have of the reschedule landing. It is
KEPT, and rendered as the fact rather than as the instruction.
"""
import pytest

from server.ticket_notes import (note_disposition, collapse_repeats,
                                 ping_summary)


# ── kept: something happened to the booking ────────────────────────────────

@pytest.mark.parametrize("body", [
    "NAR, tix are already rescheduled for +45 mins",
    "[RESCHEDULE] Automation has failed for booking 32885089. Please handle "
    "it manually.",
    "[RESCHEDULE] Reschedule was requested for 2026-08-03 date, 10:15 - "
    "timeslot but failed, please handle it manually.",
    "Booking 32885089 successfully cancelled with Reference ID HEA-97947961",
    "Refund of PLN 606.00 processed",
    "Tickets resent to the guest",
    "Pickup moved to Wielopole 2",
])
def test_a_note_recording_a_booking_fact_is_kept(body):
    assert note_disposition(body)[0] == "keep", body


def test_a_booking_fact_survives_an_admin_wrapper():
    """The NAR case, stated as a rule. "Please handle it manually" is an
    instruction; "the automation failed" is what happened, and the instruction
    around it must not take it down."""
    verdict, why = note_disposition(
        "[RESCHEDULE] Automation has failed for booking 32885089. Please "
        "handle it manually. Assigning to the supply team.")
    assert verdict == "keep", why


# ── dropped: pure ticket administration ────────────────────────────────────

@pytest.mark.parametrize("body,kind", [
    ("Please close this ticket once the guest confirms", "disposition"),
    ("Moving to pending", "disposition"),
    ("Assigning to the supply team", "disposition"),
    ("Re-assigning to Mariya", "disposition"),
    ("Added the vip tag", "field-or-tag"),
    ("Applied a priority tag", "field-or-tag"),
    ("Macro applied", "field-or-tag"),
    ("SLA breach warning", "sla-reminder"),
    ("Please update the ticket", "sla-reminder"),
    ("Thanks", "signature-or-empty"),
    ("--", "signature-or-empty"),
])
def test_pure_ticket_administration_is_dropped(body, kind):
    verdict, why = note_disposition(body)
    assert verdict == "drop", (body, why)
    assert kind in why, (body, why)


def test_an_empty_note_is_dropped_and_says_why():
    assert note_disposition("") == ("drop", "empty comment")
    assert note_disposition("   ")[0] == "drop"


# ── judged: the patterns are not certain ───────────────────────────────────

@pytest.mark.parametrize("body", [
    "Guest says the coach never arrived",
    "Spoke to the partner on WhatsApp, awaiting their answer",
    "Checked the vendor portal, nothing there",
])
def test_an_unenumerable_note_is_left_to_the_model(body):
    """A novel phrasing cannot be pattern-matched, and inventing a rule for it
    is how a real event gets dropped. The model is given the rule in words and
    told to keep when unsure."""
    assert note_disposition(body)[0] == "judge", body


def test_dropping_is_never_the_default():
    """On uncertainty the verdict is 'judge', never 'drop'. A kept clutter row
    is visible and arguable; a dropped event is unrecoverable."""
    assert note_disposition("something nobody anticipated")[0] != "drop"


def test_every_verdict_carries_a_reason():
    for body in ("NAR, tix rescheduled", "Please close this ticket", "", "xyz"):
        verdict, why = note_disposition(body)
        assert verdict in ("keep", "drop", "judge"), body
        assert why.strip(), body


# ── repeated system pings collapse ─────────────────────────────────────────

PING = "Customer Reschedule Request can't be pushed to Pending until it's pending on SP."


def _ev(t, body, internal=True):
    return {"is_internal": internal, "time": t, "raw_body": body}


def test_four_identical_pings_become_one_entry():
    """The repetition is the signal; the individual lines are not. Four rows
    saying one thing push the events that matter off the screen."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING), _ev("02 Aug 15:22", PING),
        _ev("02 Aug 19:02", PING), _ev("03 Aug 10:07", PING)])
    assert len([e for e in kept if PING in e["raw_body"]]) == 1, kept
    assert len(groups) == 1
    assert groups[0]["count"] == 4


def test_the_collapsed_entry_carries_the_count_and_the_span():
    """A reader needs to know it kept happening and for how long — which is
    exactly what four identical rows fail to say."""
    _, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING), _ev("02 Aug 15:22", PING),
        _ev("02 Aug 19:02", PING), _ev("03 Aug 10:07", PING)])
    line = ping_summary(groups[0])
    assert "4 system pings" in line, line
    assert "02 Aug 09:14" in line and "03 Aug 10:07" in line, line


def test_pings_collapse_even_when_other_events_sit_between_them():
    """They interleave with real events. Requiring adjacency would leave four
    near-identical rows on screen whenever anything happened in between."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING),
        _ev("02 Aug 10:00", "Guest replied", internal=False),
        _ev("02 Aug 15:22", PING)])
    assert len(groups) == 1 and groups[0]["count"] == 2
    assert any(e["raw_body"] == "Guest replied" for e in kept)


def test_pings_differing_only_by_an_id_still_collapse():
    """The ids are what make them look distinct; the message is the same."""
    _, groups = collapse_repeats([
        _ev("02 Aug 09:14", "Automation failed for booking 32885089"),
        _ev("02 Aug 15:22", "Automation failed for booking 32885090")])
    assert groups and groups[0]["count"] == 2


def test_a_single_occurrence_is_not_collapsed():
    """One automated message is an event, not a repetition."""
    kept, groups = collapse_repeats([_ev("02 Aug 09:14", PING)])
    assert groups == []
    assert len(kept) == 1


def test_two_different_messages_do_not_collapse_into_each_other():
    """An over-eager collapse hides work, which is the inverse bug and just as
    bad."""
    _, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING),
        _ev("02 Aug 15:22", "Refund of PLN 606.00 processed")])
    assert groups == []


def test_guest_facing_events_are_never_collapsed():
    """Only machinery repeats meaninglessly. A guest who wrote the same thing
    twice wrote it twice, and that is a fact about the guest."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", "Any update?", internal=False),
        _ev("02 Aug 15:22", "Any update?", internal=False)])
    assert groups == []
    assert len(kept) == 2


def test_a_span_of_one_moment_does_not_read_as_a_range():
    line = ping_summary({"count": 2, "from": "02 Aug 09:14", "to": "02 Aug 09:14"})
    assert "to" not in line.replace("Aug", ""), line


# ── what makes two pings "the same" ────────────────────────────────────────
#
# TWO MUTATIONS SURVIVED THE WHOLE SUITE HERE, and the reason is visible in
# the tests above: every one of them collapses the SAME STRING repeated. When
# the bodies are already identical `_ping_key` has nothing to normalise, so
# deleting the digit-stripping passed, and deleting the punctuation-stripping
# passed too.
#
# The function exists for the case those tests do not have. The pings carry
# ticket numbers and timestamps, so the four rows on a real card were never
# byte-identical — normalising is the whole reason they collapse at all.

def test_pings_differing_only_by_an_id_still_collapse():
    """The real ones carry a ticket number. Without the digit-stripping they
    are four distinct strings and four rows stay on screen — which is the
    state this function was written to end."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", f"{PING} (ref 33978941)"),
        _ev("02 Aug 15:22", f"{PING} (ref 33978955)"),
        _ev("03 Aug 10:07", f"{PING} (ref 34335318)")])
    assert len(groups) == 1, groups
    assert groups[0]["count"] == 3


def test_pings_differing_only_by_a_timestamp_still_collapse():
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", f"{PING} at 09:14"),
        _ev("02 Aug 15:22", f"{PING} at 15:22")])
    assert len(groups) == 1 and groups[0]["count"] == 2, groups


def test_pings_differing_only_by_punctuation_still_collapse():
    """The same message re-sent by a different system arrives with different
    spacing and brackets. Two rows for one event is the same defect as four."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING),
        _ev("02 Aug 15:22", "  Customer Reschedule Request -- can't be pushed "
                            "to Pending, until it's pending on SP!!  ")])
    assert len(groups) == 1 and groups[0]["count"] == 2, groups


def test_two_genuinely_different_pings_do_not_collapse():
    """THE INVERSE, and it is what stops the normalisation going too far. A
    key that strips enough to merge these would hide a second, different
    failure behind the first one's count."""
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", PING),
        _ev("02 Aug 15:22", "Refund of GBP 5.12 could not be processed.")])
    assert groups == [], groups
    assert len(kept) == 2, kept


def test_the_number_itself_is_not_what_distinguishes_them():
    """`_ping_key` maps every run of digits to a single '#', so "ref 1" and
    "ref 33978941" are one key. A length-sensitive replacement would leave
    them apart."""
    from server.ticket_notes import _ping_key
    assert _ping_key(f"{PING} ref 1") == _ping_key(f"{PING} ref 33978941")
    assert _ping_key(PING) != _ping_key("Refund could not be processed")


def test_a_ping_with_a_number_is_not_the_same_as_one_without():
    """WHAT THE DIGIT STEP ACTUALLY BUYS, and the last mutation to survive
    here. The second regex already strips digits — they are not in `[a-z# ]`,
    so they become spaces either way, and every test above passes without the
    first step at all.

    Mapping digits to '#' FIRST keeps "there was a number here" as a
    distinguishing feature. Without it "Refund of 5 tickets processed" and
    "Refund of tickets processed" normalise to the same string and collapse
    into one row — two different messages reported as one repeated one, with
    the count hiding the difference."""
    from server.ticket_notes import _ping_key
    assert _ping_key("Refund of 5 tickets processed") \
        != _ping_key("Refund of tickets processed")
    kept, groups = collapse_repeats([
        _ev("02 Aug 09:14", "Refund of 5 tickets processed"),
        _ev("02 Aug 15:22", "Refund of tickets processed")])
    assert groups == [], groups
    assert len(kept) == 2, kept
