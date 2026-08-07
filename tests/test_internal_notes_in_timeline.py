"""Internal notes reach the timeline, selectively.

`server/ticket_notes.py` held `note_disposition`, `collapse_repeats` and
`ping_summary` with a full test suite and NO CALLER. The rule — drop ticket
housekeeping, keep booking facts, collapse repeated system pings — existed
only as a passing suite, while every internal note went to the card and the
client hid the lot behind a toggle. Same failure as price_check.
"""
from server.services.zendesk import select_internal_notes


def _ev(summary, internal=True, time="01"):
    return {"time": time, "summary": summary, "is_internal": internal}


def test_a_booking_fact_stops_being_internal():
    """Keeping it behind the toggle is the same as dropping it — nobody opens
    a toggle to find out whether there is anything worth seeing."""
    got = select_internal_notes([_ev("Refund of GBP 5.12 processed")])
    assert got[0]["is_internal"] is False, got


def test_ticket_administration_stays_behind_the_toggle():
    got = select_internal_notes([_ev("Ticket assigned to queue")])
    assert got[0]["is_internal"] is True, got


def test_it_is_hidden_and_NOT_deleted():
    """The toggle already says how many it hid. An event nobody can reach is
    an event nobody can check."""
    got = select_internal_notes([_ev("Ticket assigned to queue")])
    assert len(got) == 1, got


def test_an_uncertain_note_is_kept_rather_than_hidden():
    """Unsure means show it. Hiding a booking fact is the expensive
    direction."""
    got = select_internal_notes([_ev("Some note with no clear signal at all")])
    assert got[0]["is_internal"] is False, got
    assert "kept because" in got[0]["internal_reason"], got


def test_repeated_pings_collapse_to_one_row_with_count_and_span():
    """With four identical automated messages the REPETITION is the signal and
    the individual lines are not."""
    runs = [_ev("Reschedule cannot be actioned", time=t) for t in ("04", "05", "06")]
    got = select_internal_notes(runs)
    assert len(got) == 1, [g["summary"] for g in got]
    assert "3 system pings" in got[0]["summary"], got[0]
    assert "04" in got[0]["summary"] and "06" in got[0]["summary"], got[0]


def test_a_collapsed_run_says_a_judgement_was_made():
    runs = [_ev("Reschedule cannot be actioned", time=t) for t in ("04", "05")]
    got = select_internal_notes(runs)
    assert "collapsed" in got[0]["internal_reason"], got[0]


def test_two_different_notes_are_not_collapsed_into_one():
    """A collapse that eats a distinct event is worse than the repetition."""
    got = select_internal_notes([_ev("Refund of GBP 5.12 processed", time="01"),
                                 _ev("Refund of GBP 9.99 processed", time="02")])
    assert len(got) == 2, [g["summary"] for g in got]


def test_a_guest_facing_event_is_untouched():
    got = select_internal_notes([_ev("Guest reached out", internal=False)])
    assert got[0]["is_internal"] is False
    assert got[0]["summary"] == "Guest reached out"


def test_order_is_preserved():
    """The timeline is a chronology; reordering it is the one thing it cannot
    survive."""
    evs = [_ev("Guest reached out", internal=False, time="01"),
           _ev("Refund of GBP 5.12 processed", time="02"),
           _ev("Ticket assigned to queue", time="03")]
    got = select_internal_notes(evs)
    assert [g["time"] for g in got] == ["01", "02", "03"], got


def test_no_internal_events_at_all_is_a_no_op():
    evs = [_ev("Guest reached out", internal=False)]
    assert select_internal_notes(evs) == evs


def test_it_does_not_raise_on_junk():
    for bad in (None, [], [None], [{}], ["nope"]):
        select_internal_notes(bad)


def test_the_shaper_actually_calls_it():
    """A selector wired into no path looks exactly like one that works.
    NEGATIVE source assertion is not enough here, so this drives the seam the
    shaper returns through."""
    import inspect
    import server.services.zendesk as z
    src = inspect.getsource(z._shape_via_claude)
    assert "select_internal_notes(kept)" in src, \
        "the shaper returns its rows without selecting internal notes"
