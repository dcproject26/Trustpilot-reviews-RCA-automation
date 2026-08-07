"""§1 has to say why the guest reached out and whether we solved it.

It could not. `rca_v3_prompt` was given `support_summary` — one worked-out
line about the arc of the case — and nothing else about the tickets. That is
enough to say a conversation happened and not enough to answer either
question. A guest does not contact us without a reason, and the reason is in
the ticket, which the model never saw.

Driven through `_support_tickets_block` and through the assembled prompt, so
"the block exists" and "the block reaches the model" are separate assertions —
the first has been true of things the second was false of.
"""
from server.prompts import _support_tickets_block, rca_v3_prompt


CHAT = {"time": "02 Aug 15:36", "ticket_id": "34335318", "thread": "chat",
        "actor": "guest", "guestSaid": "Krakville told me 13:45, not 11:00",
        "weDid": "Confirmed the rescheduling window had closed"}
BOOKING_DUMP = {"time": "03 Aug 15:53", "thread": "email", "actor": "system",
                "guestSaid": "2 Adults, 2 Children; EUR 203.35 paid", "weDid": ""}


def test_what_the_guest_asked_is_in_the_block():
    got = _support_tickets_block([CHAT])
    assert "Krakville told me 13:45" in got, got


def test_what_we_replied_is_in_the_block():
    got = _support_tickets_block([CHAT])
    assert "rescheduling window had closed" in got, got


def test_the_ticket_is_citable():
    """§1's evidence rows carry a ZD ref. The model cannot write one it was
    never shown."""
    assert "ZD-34335318" in _support_tickets_block([CHAT])


def test_a_contact_we_never_answered_says_so():
    """"did we solve their problem" has a third answer — we did not reply —
    and an empty string would read as a reply the block failed to render."""
    got = _support_tickets_block([dict(CHAT, weDid="")])
    assert "no reply recorded" in got, got


def test_a_booking_dump_is_not_offered_as_something_the_guest_said():
    """Same predicate as the card and the Slack post. A booking-detail row
    reaching the model as guest speech would put it into §1 as a contact."""
    got = _support_tickets_block([CHAT, BOOKING_DUMP])
    assert "EUR 203.35" not in got, got


def test_what_was_excluded_is_counted():
    got = _support_tickets_block([CHAT, BOOKING_DUMP])
    assert "1 system event(s)" in got, got


def test_no_contact_at_all_is_a_stated_result():
    """"(none)" reads as a rendering gap. This is a fact about the booking and
    it is one §1 has to report."""
    assert "no support contact was found" in _support_tickets_block([])


def test_only_machinery_reads_as_nobody_spoke_to_them():
    """Different from the line above, and the difference is the whole point:
    there were events on this booking and none of them was a person."""
    got = _support_tickets_block([BOOKING_DUMP])
    assert "nobody spoke to them" in got, got
    assert "no support contact was found" not in got, got


def test_a_long_case_says_how_much_it_is_not_showing():
    """A truncated list that does not announce itself reads as the whole of
    the case, and §1 would be written against a case that looks smaller."""
    got = _support_tickets_block([dict(CHAT, guestSaid=f"turn {i}")
                                  for i in range(25)])
    assert "further contact(s) not shown" in got, got


def test_the_block_actually_reaches_the_assembled_prompt():
    """The one that matters. A block built and never interpolated is the
    failure this codebase opens with — and this prompt already had a section
    that was 'pasted in here and referred to by no rule, so it was ignored on
    every card'."""
    out = rca_v3_prompt(
        review_text="late tickets", booking={}, timeline=[], insights={},
        dss_rec={}, l1="", l2="", sub_theme="", support_summary="",
        checklist={}, support_frames=[CHAT])
    assert "Krakville told me 13:45" in out
    assert "ZD-34335318" in out
    assert "<<SUPPORT_TICKETS>>" not in out, "the placeholder was not filled"


def test_the_prompt_still_assembles_with_no_frames():
    out = rca_v3_prompt(
        review_text="late tickets", booking={}, timeline=[], insights={},
        dss_rec={}, l1="", l2="", sub_theme="", support_summary="",
        checklist={})
    assert "no support contact was found" in out
    assert "<<SUPPORT_TICKETS>>" not in out
