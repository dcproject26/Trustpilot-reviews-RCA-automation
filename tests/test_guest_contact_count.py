"""How many times did the guest reach us — one number, grouped one way.

The reported bug was contacts going missing between what Zendesk holds and what
the RCA renders. The reconciliation that catches it (rca_v4_validate.
contact_join_notes) needs a count of distinct guest contacts, and that count
has to be grouped the SAME way the card groups them — one entry per ticket, with
a time-window fallback for untracked frames — or "N found" would disagree with
the N a reader counts on the card, which is a second silent drop by another
route.

`zendesk.group_contacts` is the one grouping; `slack._contacts` renders from it.
These drive both and assert they never disagree, the way the codebase guards
every join key it keeps two users of.
"""
from server.services.zendesk import group_contacts, guest_contact_count


def _guest(tid, **over):
    fr = {"ticket_id": str(tid) if tid is not None else None,
          "thread": "chat", "actor": "guest", "guestSaid": f"wrote on {tid}"}
    fr.update(over)
    return fr


# ── the count ────────────────────────────────────────────────────────────────

def test_each_distinct_ticket_is_one_contact():
    """The reproduction shape: many separate tickets, each a guest chat/call/
    email, is that many contacts (booking 33465545 had 19 of them)."""
    frames = [_guest(t) for t in range(1, 20)]          # 19 distinct tickets
    assert guest_contact_count(frames) == 19


def test_many_frames_on_one_ticket_are_one_contact():
    """A nine-message chat is one contact, not nine — the count is per contact,
    not per event, or it reports events and reads as inflated."""
    frames = [_guest(1, guestSaid=f"m{i}") for i in range(9)]
    assert guest_contact_count(frames) == 1


def test_a_ticket_the_guest_was_never_in_is_not_a_contact():
    """guest_took_part: an agent-only ticket is machinery on our side, not a
    guest reaching us."""
    frames = [{"ticket_id": "9", "thread": "email", "actor": "co",
               "weDid": "internal note"}]
    assert guest_contact_count(frames) == 0


def test_machinery_frames_do_not_count():
    """The booking thread is not a conversation, whatever channel it wears."""
    frames = [{"ticket_id": "1", "thread": "booking", "actor": "system",
               "guestSaid": "2 Adults; vendor API"},
              _guest(2)]
    assert guest_contact_count(frames) == 1


def test_untracked_frames_within_the_window_are_one_contact():
    """Consecutive messages minutes apart with no ticket id are one exchange."""
    frames = [{"ticket_id": None, "thread": "call", "actor": "guest",
               "guestSaid": "a", "time_sort": "2026-07-22T15:41:00+00:00"},
              {"ticket_id": None, "thread": "call", "actor": "guest",
               "guestSaid": "b", "time_sort": "2026-07-22T15:49:00+00:00"}]
    assert guest_contact_count(frames) == 1


def test_untracked_frames_outside_the_window_are_separate_contacts():
    frames = [{"ticket_id": None, "thread": "call", "actor": "guest",
               "guestSaid": "a", "time_sort": "2026-07-22T15:41:00+00:00"},
              {"ticket_id": None, "thread": "call", "actor": "guest",
               "guestSaid": "b", "time_sort": "2026-07-22T19:00:00+00:00"}]
    assert guest_contact_count(frames) == 2


def test_none_and_junk_do_not_raise():
    assert guest_contact_count(None) == 0
    assert guest_contact_count([None, "nope", 42]) == 0


# ── group_contacts shape ──────────────────────────────────────────────────────

def test_group_contacts_keys_on_the_ticket_digits():
    """'ZD-4491' and '4491' are the same ticket — the join key the renderer uses
    is digits, so the grouping has to be too."""
    groups = group_contacts([_guest("ZD-4491"), _guest("4491")])
    assert len(groups) == 1
    assert groups[0][0] == "4491"
    assert len(groups[0][1]) == 2


# ── the agreement guard: group_contacts and slack._contacts never disagree ────

def _shape(groups):
    """(key, size) per group — enough to catch any grouping drift."""
    return [(k, len(g)) for k, g in groups]


def test_group_contacts_agrees_with_the_slack_composer():
    """services/slack.py::_contacts is the twin that renders the section. If the
    two grouping rules drift, the count on the trail and the rows on the card
    stop meaning the same thing — the exact failure zd_key's single home exists
    to prevent."""
    from server.services.slack import _contacts
    cases = [
        [_guest(1), _guest(2), _guest(3)],
        [_guest(1, guestSaid=f"m{i}") for i in range(4)] + [_guest(2)],
        [_guest("ZD-7"), _guest("7"), _guest(8)],
        [{"ticket_id": None, "thread": "call", "actor": "guest",
          "guestSaid": "a", "time_sort": "2026-07-22T15:41:00+00:00"},
         {"ticket_id": None, "thread": "call", "actor": "guest",
          "guestSaid": "b", "time_sort": "2026-07-22T15:49:00+00:00"},
         {"ticket_id": None, "thread": "call", "actor": "guest",
          "guestSaid": "c", "time_sort": "2026-07-22T19:00:00+00:00"}],
        [],
    ]
    for frames in cases:
        assert _shape(group_contacts(frames)) == _shape(_contacts(frames)), frames
