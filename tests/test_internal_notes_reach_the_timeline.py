"""The note recording that the guest rescheduled was never in our data.

    if getattr(c, "public", True) is False:
        continue

Zendesk marks an internal note public=False, and that `continue` dropped it
before it became a raw event. It was missing from the events timeline and from
case findings, and NOTHING SAID SO — because everything downstream then ran
correctly on nothing:

  * the shaping prompt says "KEEP EVERY EVENT … do NOT drop machinery", and
    the model kept every event it was shown;
  * `select_internal_notes` opens `if not internal: return rows` — it ran,
    found none, returned unchanged. Ran-and-found-nothing looked exactly like
    never-ran;
  * the internal-events toggle counts what it withheld, so with nothing marked
    internal there was no toggle and no count.

And it disguised itself: automated senders post PUBLIC comments, so `api` and
`sp` machinery rows still rendered. The timeline looked like it carried
internal events while the private ones were gone.

`note_disposition`, `collapse_repeats`, `ping_summary` and
`select_internal_notes` were all written to sort exactly these notes into
keep/drop/judge. A whole subsystem, fed by a filter that removed its input.
"""
import pytest

from server.services.zendesk import select_internal_notes, is_conversation


def _note(text, internal=True, **kw):
    base = {"time": "02 Aug 09:11", "time_sort": "2026-08-02T09:11:00",
            "thread": "email", "actor": "co", "ticket_id": "33978941",
            "label": text[:60], "summary": text,
            "is_internal": internal,
            "internal_reason": "Zendesk internal note — not visible to the guest"
                               if internal else ""}
    base.update(kw)
    return base


# ── a private note is kept and marked ──────────────────────────────────────

def test_a_private_comment_is_no_longer_dropped_at_the_fetch():
    """NEGATIVE source assertion. The fetch loop is inside a Zendesk-client
    call that cannot be driven here, and the whole defect was ONE `continue`
    — so what is asserted is that the skip is gone, which unreachable code
    cannot satisfy."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert 'if getattr(c, "public", True) is False:\n                continue' not in src, \
        "private comments are being discarded before they become events again"


def test_a_private_comment_is_marked_internal():
    """Kept is not enough: an unmarked note renders inline as though the guest
    could see it, which is what the old `continue` was avoiding. The marking
    is what makes keeping it safe."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert "if _is_private and not reason:" in src, \
        "a private comment with no machinery pattern in its body would be " \
        "marked public and rendered as guest-facing"


# ── what select_internal_notes does once it HAS input ──────────────────────

def test_a_booking_fact_is_promoted_to_render_inline():
    """The reason for keeping them. A note saying what happened to the booking
    stops being internal so the reader sees it without opening a toggle."""
    rows = select_internal_notes([
        _note("Guest rescheduled to 11:00 on the same booking reference")])
    assert len(rows) == 1
    assert not rows[0]["is_internal"], rows[0]


def test_ticket_housekeeping_stays_behind_the_toggle():
    """Not deleted — the toggle says how many it hid, and an event nobody can
    reach is one nobody can check."""
    rows = select_internal_notes([_note("Ticket assigned to Tier 2 queue")])
    assert len(rows) == 1
    assert rows[0]["is_internal"], rows[0]


def test_nothing_is_deleted_by_the_selector():
    rows = select_internal_notes([
        _note("Guest rescheduled to 11:00"),
        _note("Ticket assigned to Tier 2 queue"),
        _note("Guest emailed asking about pickup", internal=False)])
    assert len(rows) == 3, rows


def test_an_empty_list_is_returned_unchanged():
    assert select_internal_notes([]) == []


def test_a_timeline_with_no_internal_rows_is_untouched():
    """The early return that made the whole thing invisible. It is correct —
    what was wrong was that it was ALWAYS the path taken."""
    rows = [_note("Guest emailed asking about pickup", internal=False)]
    assert select_internal_notes(list(rows)) == rows


# ── a kept note must not become a guest contact ────────────────────────────

def test_a_promoted_note_is_still_not_a_conversation():
    """Rendering inline on the TIMELINE is not the same as being a contact.
    A note written by an agent to themselves must not raise the contact count
    or reach 'Customer / CE interactions'."""
    row = select_internal_notes([
        _note("Guest rescheduled to 11:00 on the same booking reference",
              actor="system")])[0]
    assert is_conversation(row) is False, row


def test_a_real_guest_message_is_still_a_conversation():
    assert is_conversation(_note("Where are my tickets?", internal=False,
                                 actor="guest")) is True
