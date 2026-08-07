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


# ── a real chat is not bookkeeping because it mentions a session ───────────

from server.services.zendesk import _machine_body_reason


TRANSCRIPT = (
    "Chat transcript\nConversation started at 15:36.\n"
    "Guest said the new vendor told her pickup was at 13:45, and she asked to "
    "revert to 08:30 or any time before 11:00. The AI bot acknowledged the "
    "issue and passed to an agent. Agent confirmed the rescheduling window had "
    "closed and no time change was possible. Agent provided the vendor's "
    "contact number only. No escalation to SP or internally was raised. Guest "
    "expressed dissatisfaction and asked for a refund instead.")


def test_a_real_transcript_is_not_machinery():
    """THE REPORTED SYMPTOM. The chat on ZD-34335318 was classified machinery
    because its transcript opens with "Chat transcript" and "Conversation
    started" — so `_detect_actor` returned "system", `is_internal` went true,
    `is_conversation` rejected it, the ticket produced NO contact frame, and
    the model's note for it rendered badged "unmatched ZD reference".

    A broken join reported as a reference the model got wrong."""
    assert _machine_body_reason(TRANSCRIPT) == "", _machine_body_reason(TRANSCRIPT)


def test_a_bare_session_line_is_still_bookkeeping():
    """The pattern is kept — a body that IS the boilerplate is exactly what it
    was written for."""
    assert _machine_body_reason(
        "Conversation started at 15:36. Session id: a1b2c3") == "chat-bookkeeping"


def test_length_does_not_excuse_the_other_patterns():
    """Only the bookkeeping patterns are length-sensitive. The rest name
    things a person does not write, and a long one is still machinery."""
    assert _machine_body_reason("login credential: abc " + "x" * 500) == "credentials"


def test_a_long_body_can_still_match_a_later_pattern():
    """Skipping a length-sensitive match must keep looking, not return clean —
    a transcript that ALSO dumps credentials is still machinery."""
    body = TRANSCRIPT + "\nvendor login: portal-user / password: hunter2"
    assert _machine_body_reason(body) == "credentials", _machine_body_reason(body)


# ── only the notes that record a booking fact ─────────────────────────────

from server.ticket_notes import note_disposition


def test_a_reschedule_note_is_kept():
    """What was actually asked for."""
    assert note_disposition("Guest rescheduled to 11:00")[0] == "keep"


def test_a_cancellation_note_is_kept():
    assert note_disposition("Original 08:30 slot cancelled via API")[0] == "keep"


def test_a_refund_note_is_kept():
    assert note_disposition("Refund of EUR 39.59 processed to wallet")[0] == "keep"


def test_ticket_administration_is_dropped():
    """Keeping EVERY private comment sent agent macros, queue moves and full
    HTML mail bodies into one shaping prompt. It came back unparseable and
    `_fallback_shape` rendered RAW BODIES — "![Logo](https://cdn-imgix-open…)"
    under a "System event" label — for the whole timeline."""
    assert note_disposition("Ticket assigned to Tier 2 queue")[0] == "drop"


def test_an_unclear_note_is_kept_not_dropped():
    """Unsure means show it. Hiding a booking fact is the expensive
    direction."""
    assert note_disposition("Spoke to the vendor about this one")[0] == "judge"


def test_the_fetch_applies_the_disposition_rule():
    """WIRING. `note_disposition` was being applied AFTER shaping — too late
    to keep the payload sane, which is what broke the timeline."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    i = src.index("_is_private = getattr(c,")
    j = src.index("events.append((", i)
    assert "note_disposition(body)" in src[i:j], \
        "the fetch keeps every private comment again"


def test_a_failed_shaping_marks_its_rows():
    """A fallback timeline is raw bodies under category labels, and it rendered
    in the same rows as a shaped one — so a failed model call read as a
    redesign of the card."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert 'r["shaping_failed"] = True' in src, \
        "a fallback timeline is indistinguishable from a shaped one again"


def test_the_trail_says_the_timeline_was_not_summarised():
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert "The events timeline was not summarised" in src
    assert 'e.get("shaping_failed")' in src


def test_the_trail_counts_the_internal_notes():
    """A note read and set aside and a note never fetched leave the same
    timeline. Only the count tells them apart — and it is the reason this took
    a screenshot to find."""
    src = open("server/pipeline.py", encoding="utf-8").read()
    assert "<strong>Internal notes:</strong>" in src


# ── the notes arrive, and the shaper must not fold them away ──────────────
#
# MEASURED with scripts/trace_notes.py on booking 32885089:
#
#     public 8 · private 18 · private_kept 18 · private_dropped 0
#
# Every internal note reached the shaping call, including
# "[RESCHEDULE] Automation has failed for booking 32885089", "NAR, tix are
# already rescheduled for +45 mins" and the ORM credit note. 26 comments went
# in; 8 rows came back. Nothing filtered them — the model collapsed them into
# the public events beside them, and the prompt gave it no reason not to.

import re

from server.prompts import zendesk_timeline_shape_prompt


def _prompt():
    return zendesk_timeline_shape_prompt({}, "review", "2026-08-02",
                                         [{"idx": 0, "is_internal": True}])


def test_the_prompt_forbids_collapsing_a_note_into_a_public_event():
    """An internal note is what Headout wrote to itself about this booking.
    Folded into the confirmation mail beside it, the only record of the
    reschedule is gone."""
    assert "NEVER COLLAPSE AN EVENT WITH is_internal" in _prompt()


def test_the_prompt_keeps_notes_that_record_something_that_happened():
    """A reschedule, a refund, a credit, an agent's own note — the record of
    what we DID about the problem."""
    assert "AN INTERNAL NOTE THAT RECORDS SOMETHING THAT HAPPENED" in _prompt()


def test_the_prompt_drops_the_tickets_own_furniture():
    """"AN INTERNAL NOTE IS NEVER keep: false" was my rule and it manufactured
    the noise: 29 raw events became 28 rows, among them "Booking details
    posted", "Booking status snapshot posted", "Support history thread opened"
    and "Credit refund comment logged". Those describe the TICKET, not the
    booking."""
    t = _prompt()
    assert "THE TICKET'S OWN FURNITURE IS NOT AN EVENT" in t
    assert "AN INTERNAL NOTE IS NEVER keep: false" not in t, \
        "the blanket keep rule is back, and it forces the furniture onto the card"


def test_the_prompt_says_the_label_examples_are_not_words_to_copy():
    """A run returned "Booking intimation sent to the supply partner" copied
    verbatim out of the example table, beside four mechanism names the table
    exists to replace."""
    assert "THESE ARE THE SHAPE, NOT THE WORDS" in _prompt()


def test_repeated_automated_notes_may_still_collapse_together():
    """Six identical "Customer Reschedule Request can't be pushed to Pending"
    lines are one action repeating — that is what the collapse rule is for,
    and the exemption must not disable it."""
    assert "Two internal notes MAY collapse together" in _prompt()


def test_the_rules_are_numbered_once_each():
    """Two rules numbered 5 is a prompt the model reads as contradicting
    itself, and it is the kind of edit that lands unnoticed."""
    nums = re.findall(r"^(\d)\. [A-Z]", _prompt(), re.M)
    assert nums == sorted(nums), nums
    assert len(nums) == len(set(nums)), nums


# ── which notes are furniture, measured on booking 32885089 ───────────────

import pytest as _pytest


@_pytest.mark.parametrize("body", [
    "Support history thread opened for Booking ID: 32885089.",
    "ITINERARY ID: 28219098 ITINERARY MARGIN: 0.0% Booking ID: 32885089",
    "------------------------------ -- Booking Info ------ Itinerary Id",
    "Conversation with ios User 6a6f11e92c9f1009e9cdf6df",
])
def test_the_tickets_own_furniture_is_dropped(body):
    """These four became timeline rows. Each describes the ticket, not the
    booking."""
    assert note_disposition(body)[0] == "drop", note_disposition(body)


@_pytest.mark.parametrize("body", [
    "[RESCHEDULE] Automation has failed for booking 32885089",
    "[RESCHEDULE] Reschedule was requested for 2026-08-03 date, 10:15 but failed",
    "NAR , tix are already rescheduled for +45 mins",
    '"<h4> Credit refund comment : </h4> ORM Escalation"',
    "Customer Reschedule Request can't be pushed to Pending until it's pending on SP.",
])
def test_what_actually_happened_is_kept(body):
    assert note_disposition(body)[0] == "keep", note_disposition(body)


def test_a_bare_booking_id_is_not_a_booking_fact():
    """`booking\\s*id\\s*\\d{6,}` was in the keep pattern, and EVERY internal
    note on a booking cites the booking — so the rule that was supposed to
    find booking facts was matching the reference number instead, and kept
    everything. A booking id is a label; the verbs are the events."""
    assert note_disposition("Reference Booking ID: 32885089")[0] != "keep"
    assert note_disposition("Booking 32885089 was cancelled")[0] == "keep"


# ── furniture that names a booking verb is still furniture ────────────────

@_pytest.mark.parametrize("body,want", [
    # A FIELD SNAPSHOT, not an event. It matched `reschedul` on the word
    # "Reschedulable" and rendered as "Booking status snapshot posted".
    ("📋 **Booking Details** **Cancellation & Rescheduling** • Is Cancellable: "
     "No • Is Reschedulable: Yes", "drop"),
    ("------------------------------ -- Booking Info ------ Itinerary Id", "drop"),
    ("━━━━ 📋 Overall Support Summary ━━━━ Last contact", "drop"),
    # An event that names a verb IS an event, and is not shaped like a form.
    ("[RESCHEDULE] Reschedule was requested for 2026-08-03 but failed", "keep"),
    ("🔺 **Escalated Conversation** Escalation Reason: the confirmed booking "
     "was rescheduled without consent", "keep"),
])
def test_administration_is_judged_before_the_booking_verbs(body, want):
    """`_BOOKING_FACT` was checked FIRST, so furniture kept its place the
    moment it happened to contain a booking verb."""
    assert note_disposition(body)[0] == want, note_disposition(body)


def test_the_gate_asks_whether_a_comment_is_internal_not_whether_it_is_private():
    """The Booking Info dump, the ITINERARY MARGIN dump and the Overall
    Support Summary are PUBLIC comments that `_internal_reason` marks as
    machinery. Gated on `_is_private`, the furniture rule never saw them.

    Whether Zendesk marked a comment private is a fact about who can SEE it.
    Whether it is machinery is a fact about what it IS."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert "if _is_private or reason:" in src, \
        "the furniture rule only sees private comments again"


# ── the repeated automated ping collapses on what the SYSTEM wrote ────────

from server.services.zendesk import select_internal_notes

_PING = ("Customer Reschedule Request can't be pushed to Pending until it's "
         "pending on SP.")


def _ping_rows(n=5):
    """n firings of one automated line, each summarised slightly differently
    by the model — which is exactly what the trace showed."""
    return [{"time": f"0{i+1} Aug 09:1{i}", "label": "Reschedule blocked",
             "summary": ("Reschedule blocked — SP confirmation pending"
                         + (f", {i} further" if i else "")),
             "raw_body": _PING, "is_internal": True,
             "internal_reason": "via:rule"} for i in range(n)]


def test_five_firings_of_one_ping_become_one_row():
    """THE REGRESSION. `collapse_repeats` keys on `raw_body or summary`, and a
    shaped row carried no raw_body — so it fell back to the model's SUMMARY.
    The model words each repeat differently, so five identical pings produced
    five different keys and none collapsed: five "Reschedule blocked" rows on
    one card, from one automated line firing five times."""
    out = select_internal_notes(_ping_rows())
    assert len(out) == 1, [r["summary"] for r in out]


def test_the_collapsed_row_says_how_many_and_over_what_span():
    """With four identical rows hidden, the repetition IS the signal. A row
    that collapses without saying so has deleted four events."""
    out = select_internal_notes(_ping_rows())
    assert "5 system pings" in out[0]["summary"], out[0]
    assert "identical pings collapsed" in out[0]["internal_reason"], out[0]


def test_a_real_note_beside_the_pings_survives():
    rows = _ping_rows() + [{
        "time": "02 Aug 15:28", "label": "Agent note",
        "summary": "Agent noted reschedule already applied at +45 mins",
        "raw_body": "NAR , tix are already rescheduled for +45 mins",
        "is_internal": True, "internal_reason": ""}]
    out = select_internal_notes(rows)
    assert len(out) == 2, [r["summary"] for r in out]
    assert any("NAR" in r["raw_body"] for r in out), out


def test_two_different_pings_do_not_collapse_together():
    """The key is the system's own text. Two different automated lines are
    two events however alike the model's wording of them is."""
    a = _ping_rows(2)
    b = _ping_rows(2)
    for r in b:
        r["raw_body"] = "Refund automation could not reach the vendor."
    out = select_internal_notes(a + b)
    assert len(out) == 2, [r["summary"] for r in out]


def test_a_single_firing_is_not_collapsed():
    """One is an event; two is a repetition. Collapsing a single ping would
    put a count on a row that never repeated."""
    out = select_internal_notes(_ping_rows(1))
    assert len(out) == 1
    assert "system pings" not in out[0]["summary"], out[0]


def test_the_shaped_row_carries_the_raw_body_to_key_on():
    """WIRING, and the half the function above cannot show: `_shape_via_claude`
    has to put the source text on the row, or `collapse_repeats` falls back to
    the model's summary and the whole thing stops working silently."""
    src = open("server/services/zendesk.py", encoding="utf-8").read()
    assert '"raw_body": (srcs[0].get("raw_body", "") if srcs else "")' in src, \
        "the shaped row no longer carries the source text to collapse on"
