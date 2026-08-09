"""A contact the guest was never in is not a contact.

THE POST THAT WAS WRONG. On a real booking the Slack section "Customer / CE
interactions" opened with:

    contact 01  ZD-33978941  2 events
       summary: Agent reviewed escalation and marked NAR
         - 02 Aug 15:28 web/co   we: Agent marked NAR; no further action
         - 03 Aug 12:45 web/co   we: ORM escalation; 25% credit

Two internal agent actions rendered as an exchange with a guest who took no
part in either. The post reported two contacts on a booking with one, and the
dashboard's Guest ↔ Support panel showed the same pair.

HOW IT GOT THERE — a flag cleared for one section changing another's meaning:

  1. `note_disposition` returns `keep` for a booking-fact internal note.
  2. The promotion clears `is_internal` so the note renders INLINE on the
     events timeline rather than behind the toggle. That is correct, and it is
     what fixed the missing reschedule records.
  3. `is_conversation` rejected machinery USING `is_internal` — the flag step
     2 had just cleared.
  4. `thread: "web"` is not in NON_CONTACT_THREADS and `actor: "co"` IS in
     _PERSON_ACTORS, so nothing else stopped it.

Confirmed by the counterfactual: the same row with `is_internal` still True is
rejected. It is a regression from the internal-notes fix, arriving through a
field the two sections share.

TWO FIXES, because one of them is the rule and the other is the guard.

  `promoted_from_internal` RECORDS the promotion. On `keep` the old code wrote
  internal_reason="", leaving a promoted note indistinguishable from a row
  that was never internal — nothing downstream COULD tell them apart even if
  it wanted to.

  `guest_took_part` asks whether the guest is in the exchange at all, and rests
  on no flag. The regression came from a shared field being repurposed; a rule
  resting on the same field can be re-broken the same way.
"""
from server.services.zendesk import (is_conversation, split_contact_frames,
                                     guest_took_part, moved_frames_note)


def _f(**kw):
    base = {"thread": "email", "actor": "co", "ticket_id": "33978941",
            "time": "02 Aug 15:28", "guestSaid": "", "weDid": "did a thing",
            "is_internal": False, "internal_reason": ""}
    base.update(kw)
    return base


GUEST = _f(thread="chat", actor="guest", ticket_id="34335318",
           guestSaid="Asked to revert to 08:30")
NAR = _f(thread="web", weDid="Agent marked NAR; no further action",
         promoted_from_internal=True)
ORM = _f(thread="web", time="03 Aug 12:45", weDid="ORM escalation; 25% credit")


# ── the promotion is recorded ──────────────────────────────────────────────

def test_a_promoted_note_is_not_a_conversation():
    assert is_conversation(NAR) is False


def test_the_same_row_without_the_marker_still_passes_the_frame_test():
    """The marker is doing the work, not some other field of NAR."""
    assert is_conversation({k: v for k, v in NAR.items()
                            if k != "promoted_from_internal"}) is True


def test_note_disposition_stamps_the_marker_on_a_kept_note():
    """Driven through the real disposition path — a marker nothing sets is a
    marker nothing reads."""
    from server.services.zendesk import select_internal_notes as _disposition_pass
    rows = _disposition_pass([
        {"is_internal": True, "summary": "Reschedule cannot be actioned for "
                                         "this booking", "thread": "web",
         "actor": "co", "ticket_id": "1"}])
    kept = [r for r in rows if not r.get("is_internal")]
    assert kept, "the note was not promoted at all — wrong fixture"
    assert kept[0].get("promoted_from_internal") is True, kept[0]


def test_a_row_that_was_never_internal_carries_no_marker():
    """The inverse: marking everything would empty the contact panel."""
    from server.services.zendesk import select_internal_notes as _disposition_pass
    rows = _disposition_pass([GUEST])
    assert not rows[0].get("promoted_from_internal"), rows[0]


# ── the guest has to be in it ──────────────────────────────────────────────

def test_an_agent_only_ticket_is_not_a_contact():
    """THE ROW FROM THE POST. Neither frame carries the marker, so this holds
    even if the promotion is never stamped."""
    convo, moved = split_contact_frames([
        _f(thread="web", weDid="Agent marked NAR"), ORM, GUEST])
    assert [f["ticket_id"] for f in convo] == ["34335318"], convo


def test_the_guest_ticket_keeps_all_of_its_frames():
    """Our replies inside an exchange the guest started are part of it."""
    reply = _f(thread="chat", actor="co", ticket_id="34335318",
               time="02 Aug 15:37", weDid="Window closed")
    convo, _ = split_contact_frames([GUEST, reply])
    assert len(convo) == 2, convo


def test_a_ticket_where_only_our_frame_quotes_the_guest_still_counts():
    """An exchange logged agent-side with the guest's words in it is the guest
    taking part. Requiring actor == guest would drop a real contact."""
    quoted = _f(thread="email", actor="co", ticket_id="99",
                guestSaid="I never got the tickets")
    convo, _ = split_contact_frames([quoted])
    assert convo == [quoted], convo


def test_guest_took_part_reads_the_group_not_one_frame():
    assert guest_took_part([NAR, GUEST]) is True
    assert guest_took_part([NAR, ORM]) is False
    assert guest_took_part([]) is False


def test_an_ungrouped_frame_is_judged_on_its_own():
    """No ticket id means no exchange to reason about. Dropping it for want of
    a guest frame would delete an off-Zendesk contact."""
    loose = _f(ticket_id="", thread="call", actor="guest", guestSaid="rang in")
    convo, _ = split_contact_frames([loose])
    assert convo == [loose], convo


# ── the count says what it dropped, and does not mislabel it ───────────────

def test_an_agent_note_is_not_reported_as_a_system_event():
    """"3 system events moved" on a card whose AGENT NOTES were the thing
    dropped tells the reader the wrong fact. Two kinds of moved row, two
    clauses."""
    _, moved = split_contact_frames([
        _f(thread="api", actor="system", weDid="confirmation email"),
        NAR, ORM, GUEST])
    said = moved_frames_note(moved)
    assert "1 system event moved" in said, said
    assert "2 agent-side notes with no guest message" in said, said


def test_a_clean_split_still_says_nothing():
    """"0 moved" on every healthy booking is the noise that stops the counts
    being read."""
    assert moved_frames_note([]) == ""


def test_only_system_events_reads_as_it_did_before():
    _, moved = split_contact_frames([
        _f(thread="api", actor="system", weDid="confirmation email"), GUEST])
    said = moved_frames_note(moved)
    assert said == "1 system event moved to the timeline", said


def test_nothing_is_dropped_silently():
    """Every frame is in exactly one of the two lists — the split must never
    lose a row."""
    rows = [_f(thread="api", actor="system"), NAR, ORM, GUEST]
    convo, moved = split_contact_frames(rows)
    assert len(convo) + len(moved) == len(rows)
    assert {id(f) for f in convo} | {id(f) for f in moved} == {id(f) for f in rows}


# ── the three my fixtures were too generous to reach ───────────────────────
#
# Each of these SURVIVED a mutation because the frame I used satisfied two
# conditions where the rule needs one. A fixture that passes for two reasons
# cannot tell you which reason the code is using.

def test_a_collapsed_ping_run_is_marked_promoted_too():
    """SURVIVED. `select_internal_notes` clears `is_internal` in TWO places —
    the disposition branch and the collapsed-run rebuild — and only the first
    was tested. A run of identical automated pings is machinery by definition;
    unmarked, it walks into the contact panel exactly as the NAR note did."""
    from server.services.zendesk import select_internal_notes
    # THE BODY MATTERS. "Reschedule cannot be actioned" is a booking fact, so
    # note_disposition KEEPS it and the disposition branch stamps the marker
    # before the collapsed rebuild is reached — the run through this branch
    # could not be observed, and the mutation survived. Ticket administration
    # is dropped by the disposition, so only the rebuild clears `is_internal`
    # here, and only it can stamp the marker.
    #
    # AND NO DIGITS IN IT. `_MONEY_IN_NOTE` exempts money notes from
    # collapsing, and its pattern is case-insensitive `[A-Z]{3}\s*\d` — so
    # "Tier 2" reads as a currency amount and the run was never collapsed at
    # all. The first fixture here said "Assigned to Tier 2 queue" and tested
    # nothing.
    pings = [{"is_internal": True, "thread": "web", "actor": "co",
              "ticket_id": "1", "time_sort": f"2026-08-0{i}T00:00:00+00:00",
              "summary": "Assigned to the specialist queue"}
             for i in (1, 2, 3)]
    rows = select_internal_notes(pings)
    promoted = [r for r in rows if not r.get("is_internal")]
    assert promoted, "no ping run was collapsed — wrong fixture"
    assert all(r.get("promoted_from_internal") for r in promoted), promoted
    assert all(not is_conversation(r) for r in promoted), promoted


def test_actor_guest_alone_is_enough():
    """SURVIVED. Every fixture carried actor=guest AND guestSaid, so dropping
    the actor check changed nothing. A guest turn logged with no transcript is
    still the guest taking part."""
    silent = _f(thread="chat", actor="guest", ticket_id="77", guestSaid="",
                weDid="")
    assert guest_took_part([silent]) is True
    convo, _ = split_contact_frames([silent])
    assert convo == [silent], convo


def test_an_ungrouped_agent_frame_is_kept_rather_than_judged():
    """SURVIVED. The ungrouped fixture had a guest in it, so it never reached
    the `not key` branch it was named for.

    THE DECISION THIS PINS. With no ticket id there is no exchange to reason
    about — the frame might be an off-Zendesk call logged agent-side. Dropping
    what cannot be judged is how a real contact disappears, so it is kept.
    Grouped frames are a different case: there the ticket IS the exchange."""
    loose = _f(ticket_id="", thread="call", actor="co", guestSaid="",
               weDid="Called the guest back")
    assert guest_took_part([loose]) is False, "the fixture has a guest in it"
    convo, _ = split_contact_frames([loose])
    assert convo == [loose], \
        "an unjudgeable frame was dropped — that is how a contact vanishes"
