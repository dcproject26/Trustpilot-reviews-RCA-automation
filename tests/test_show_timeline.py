"""tools/show_timeline.py — every "empty" says which kind of empty it is.

The tool exists to answer "why is this section blank on the card", and it can
only do that if it distinguishes the three things a blank section means: the
lookup ran and found nothing, the lookup never ran, or the lookup ran and the
join failed. One blank box, three bugs.

Driven, not grepped: the rendering is in functions that take data and return
lines, so the distinction can be tested without a database or a warehouse.
"""
from tools.show_timeline import booking_log_lines, note_lines, timeline_lines


def _text(lines):
    return " ".join(lines)


# ── booking logs ────────────────────────────────────────────────────────────

def test_a_draft_written_before_the_field_existed_says_so():
    """Not "the model returned nothing" — the model was never asked."""
    got = _text(booking_log_lines({}, None))
    assert "never written" in got
    assert "returned no events" not in got


def test_an_empty_list_is_a_model_that_returned_nothing():
    got = _text(booking_log_lines({"booking_logs": []}, None))
    assert "returned no events" in got
    assert "never written" not in got


def test_presence_beats_truthiness():
    """`if rca_v3.get("booking_logs")` reads [] and a missing key identically,
    which is the whole bug — one is the model complying, one is a run that did
    not reach the step."""
    absent = _text(booking_log_lines({"other": 1}, None))
    empty = _text(booking_log_lines({"booking_logs": []}, None))
    assert absent != empty


def test_the_column_is_used_when_rca_v3_has_no_key():
    got = _text(booking_log_lines({}, [{"time": "01 Aug", "what": "Booked"}]))
    assert "Booked" in got
    assert "never written" not in got


def test_an_undated_entry_is_marked_not_blanked():
    """"undated" is the model following rule 10b, not a missing value, and it
    must not render as the same dash a genuinely absent time gets."""
    got = _text(booking_log_lines(
        {"booking_logs": [{"time": "undated", "what": "Refund refused"},
                          {"time": "", "what": "Something else"}]}, None))
    assert "undated" in got
    assert "Refund refused" in got


# ── the zendesk timeline ────────────────────────────────────────────────────

def test_no_tickets_and_no_events_is_a_legitimate_empty():
    got = _text(timeline_lines([], []))
    assert "nothing to build a timeline from" in got
    assert "NO events" not in got


def test_tickets_but_no_events_is_a_broken_step():
    """The same blank box on the card, and the one worth waking up for."""
    got = _text(timeline_lines(["34125496"], []))
    assert "NO events" in got
    assert "ZD-34125496" in got
    assert "not a failure" not in got


def test_the_two_empties_do_not_read_the_same():
    assert _text(timeline_lines([], [])) != _text(timeline_lines(["1"], []))


def test_a_linked_ticket_that_contributed_nothing_is_counted():
    """Rule one: say what could not be done. A ticket that produced no events
    is invisible otherwise — the timeline just looks shorter."""
    got = _text(timeline_lines(
        ["111", "222"],
        [{"time": "30 Jul 12:01", "actor": "sp", "label": "SP response",
          "ticket_id": "111", "summary": "confirmed"}]))
    assert "ZD-222" in got
    assert "contributed no events" in got
    assert "ZD-111" in got


def test_nothing_is_said_when_every_ticket_contributed():
    got = _text(timeline_lines(
        ["111"],
        [{"time": "t", "actor": "sp", "label": "SP response", "ticket_id": "111"}]))
    assert "contributed no events" not in got


# ── the model's support notes ───────────────────────────────────────────────

def test_a_note_with_no_ref_is_not_reported_as_a_broken_join():
    """The model is complying with the rule that says do not invent a ref.
    Marking it as a miss would make a healthy run look faulty."""
    got = _text(note_lines([{"zd_ref": None, "summary": "guest chased"}], ["111"]))
    assert "no ZD ref" in got
    assert "not linked" not in got


def test_a_note_naming_an_unlinked_ticket_is_marked_and_kept():
    """The ZD-4491 bug, in the place it actually happened. Dropping the note
    would make a broken join look like a model that returned nothing."""
    got = _text(note_lines([{"zd_ref": "ZD-9999", "summary": "guest chased"}],
                           ["111"]))
    assert "ZD-9999" in got
    assert "guest chased" in got, "the note was dropped rather than marked"
    assert "1 note(s) name a ticket that is not linked" in got


def test_a_note_whose_ref_joins_is_left_alone():
    got = _text(note_lines([{"zd_ref": "ZD-111", "summary": "ok"}], ["111"]))
    assert "not linked" not in got
    assert "✗" not in got


def test_the_ref_joins_on_digits_not_on_the_prefix():
    """`"ZD-4491"` against ticket_id `"4491"` matched nothing and read exactly
    like a model that returned no notes."""
    got = _text(note_lines([{"zd_ref": "ZD-4491", "summary": "s"}], ["4491"]))
    assert "not linked" not in got, "the ZD- prefix is defeating the join again"


def test_no_notes_prints_no_heading_at_all():
    assert note_lines([], ["111"]) == []
    assert note_lines(None, None) == []
