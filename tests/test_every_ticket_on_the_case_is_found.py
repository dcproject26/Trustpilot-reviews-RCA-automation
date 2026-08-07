"""The card said "one contact". There were four, and two were real conversations.

MEASURED, on Bénédicte Depois' booking 33202346, by running each route by hand
in the Zendesk agent search:

    type:ticket fieldvalue:33202346   -> 1   #34382891 refund mail
    type:ticket "33202346"            -> 3   + #34383352 CHAT
                                             + #33202346 (a ticket whose
                                               NUMBER equals the booking id)
    type:ticket requester:<email>     -> 4   + #34382691 "Billets pour :
                                               London Eye…", still ON-HOLD

The booking-id custom field is set on ONE ticket of the four. The search read:

    tickets = search(f"type:ticket fieldvalue:{bid}")
    if tickets: ...
    else:       tickets = search(f'type:ticket "{bid}"')

so fieldvalue returned something, the cascade stopped, and free-text never
ran. The chat and an OPEN contact were invisible, and the RCA was written
about a case it had seen a quarter of.

Nothing was broken in the sense of raising. The card truthfully reported what
it found, having looked in the one place that did not have the answer — which
is why no test caught it and no log line mentioned it.

`search` is injected, so these run without Zendesk.
"""
import pytest

from server.services.zendesk import collect_tickets, collect_trail, requester_email


class _T:
    def __init__(self, tid, email=""):
        self.id = tid
        self.requester = type("U", (), {"email": email})()


def _searcher(by_query):
    """A fake Zendesk that answers each query with its own list."""
    calls = []

    def _search(q):
        calls.append(q)
        for frag, rows in by_query.items():
            if frag in q:
                return list(rows)
        return []
    _search.calls = calls
    return _search


# The real case, as measured.
REFUND = _T("34382891", "benedicte_depois@yahoo.fr")
CHAT   = _T("34383352")
SYSTEM = _T("33202346")
ONHOLD = _T("34382691")
HISTORY = _T("34384372")

CASE = {
    "fieldvalue:33202346": [REFUND],
    '"33202346"':          [SYSTEM, REFUND, CHAT],
    "requester:":          [HISTORY, REFUND, ONHOLD, CHAT],
}


def _sig_email(t):
    return {"guest_email": getattr(t.requester, "email", "")}


# ── the reported case ───────────────────────────────────────────────────────

def test_the_chat_and_the_open_contact_are_both_found():
    """The whole defect, in one assertion. Four tickets, not one."""
    got, tally = collect_tickets("33202346", _searcher(CASE),
                                 lambda ts: requester_email(ts, _sig_email))
    ids = sorted(str(t.id) for t in got)
    assert "34383352" in ids, "the chat is still invisible"
    assert "34382691" in ids, "the On-hold contact is still invisible"
    # FOUR. The measured free-text search also returned ticket #33202346 —
    # whose NUMBER is the booking id — and that one is excluded by the
    # collision guard below, not carried. It is a different thing that happens
    # to be the same number.
    assert len(ids) == 4, ids


def test_free_text_runs_even_when_the_field_search_succeeded():
    """The short-circuit itself. `if tickets: ... else:` is what hid the chat,
    and a fieldvalue hit must no longer end the search."""
    s = _searcher(CASE)
    collect_tickets("33202346", s, lambda ts: requester_email(ts, _sig_email))
    assert any("fieldvalue:" in q for q in s.calls)
    assert any('"33202346"' in q for q in s.calls), \
        "free-text did not run because the field search found something"


def test_the_requester_search_runs_too():
    s = _searcher(CASE)
    collect_tickets("33202346", s, lambda ts: requester_email(ts, _sig_email))
    assert any("requester:benedicte_depois@yahoo.fr" in q for q in s.calls), s.calls


def test_a_ticket_found_twice_is_carried_once():
    """All three routes return the refund mail. The picker, the timeline and
    the contact count all read this list."""
    got, tally = collect_tickets("33202346", _searcher(CASE),
                                 lambda ts: requester_email(ts, _sig_email))
    ids = [str(t.id) for t in got]
    assert len(ids) == len(set(ids)), ids
    assert tally["duplicates"] == 3, tally


# ── the email comes off the tickets, not from a human ───────────────────────

def test_the_email_is_read_from_the_ticket_field_first():
    """"Customer Email" is filled in per booking by the desk, beside Tour Name
    and City. It is the guest; the requester address can be whoever forwarded
    the mail."""
    t = _T("1", "")
    assert requester_email([t], lambda x: {"guest_email": "guest@example.com"}) \
        == "guest@example.com"


def test_the_requester_address_is_the_fallback():
    """Structural — every ticket has a requester — so it covers the tickets
    where the custom field was never filled in."""
    assert requester_email([_T("1", "fallback@example.com")],
                           lambda x: {"guest_email": ""}) == "fallback@example.com"


def test_a_field_that_is_not_an_address_is_not_used_as_one():
    """`requester:Bhayani Salim` is a different search with different results;
    sending a name down the email route would quietly widen it."""
    assert requester_email([_T("1", "")],
                           lambda x: {"guest_email": "not an address"}) == ""


def test_no_email_anywhere_is_not_an_error():
    assert requester_email([_T("1", "")], lambda x: {"guest_email": ""}) == ""


def test_the_requester_route_is_skipped_and_SAID_when_there_is_no_email():
    """The pair that must not collapse: a requester search that found nothing
    and one that never ran. The second means contacts filed under another
    booking id are still missing, which is a reason to go and look."""
    s = _searcher({"fieldvalue:1": [_T("9", "")]})
    got, tally = collect_tickets("1", s, lambda ts: requester_email(ts, lambda x: {}))
    assert tally["requester_skipped"] is True
    assert not any("requester:" in q for q in s.calls)


def test_the_requester_route_is_not_attempted_with_nothing_to_read():
    """No tickets at all means no email to find, and searching
    `requester:` with an empty address matches on nothing useful."""
    s = _searcher({})
    got, tally = collect_tickets("1", s)
    assert got == []
    assert not any("requester:" in q for q in s.calls)


# ── the trail says which routes ran ─────────────────────────────────────────

def test_the_trail_counts_what_each_route_contributed():
    _, tally = collect_tickets("33202346", _searcher(CASE),
                               lambda ts: requester_email(ts, _sig_email))
    line = collect_trail("33202346", tally)
    assert line, "nothing on the trail about a search that found 4 more"
    assert "1 by booking-id field" in line["text"], line
    assert "more by searching the text" in line["text"], line
    assert "more from the same requester" in line["text"], line


def test_the_trail_warns_that_a_requester_hit_may_be_another_trip():
    """The cost of this route, stated where the reader is. A frequent
    traveller's tickets are about whatever they last complained about."""
    _, tally = collect_tickets("33202346", _searcher(CASE),
                               lambda ts: requester_email(ts, _sig_email))
    assert "another trip" in collect_trail("33202346", tally)["text"]


def test_a_skipped_requester_search_is_a_warning_not_a_pass():
    s = _searcher({"fieldvalue:1": [_T("9", "")]})
    _, tally = collect_tickets("1", s, lambda ts: requester_email(ts, lambda x: {}))
    line = collect_trail("1", tally)
    assert line["mark"] == "warn", line
    assert "did NOT run" in line["text"], line


def test_one_route_finding_everything_adds_no_trail_noise():
    """"and two other searches agreed" on every healthy case is the noise that
    makes a reader stop reading the counts that matter."""
    one = _T("9", "a@b.com")
    s = _searcher({"fieldvalue:1": [one], '"1"': [one], "requester:": [one]})
    _, tally = collect_tickets("1", s, lambda ts: requester_email(ts, _sig_email))
    assert collect_trail("1", tally) is None, tally


# ── it does not fall over ───────────────────────────────────────────────────

def test_a_route_that_returns_nothing_does_not_stop_the_others():
    s = _searcher({'"1"': [_T("5", "a@b.com")]})
    got, tally = collect_tickets("1", s, lambda ts: requester_email(ts, _sig_email))
    assert [str(t.id) for t in got] == ["5"]
    assert tally["fieldvalue"] == 0 and tally["free_text"] == 1


def test_a_ticket_with_no_id_is_still_carried():
    """Dedupe keys on the id; a row without one must not be silently dropped
    by the mechanism that exists to stop duplicates."""
    class _NoId:
        pass
    got, _ = collect_tickets("1", _searcher({"fieldvalue:1": [_NoId()]}),
                             lambda ts: "")
    assert len(got) == 1


# ── the ticket id / booking id collision ───────────────────────────────────

def test_a_ticket_whose_number_equals_the_booking_id_is_excluded():
    """THE CHRONOLOGY BUG. Zendesk ticket ids and Headout booking ids share
    the same numeric space, so free-text "32885089" matched TICKET #32885089 —
    an unrelated German-language chat from 11 Jun — and put it at the top of a
    timeline whose booking was not confirmed until 21 Jul.

    A month before the booking existed, above every real event, with the case
    findings written from it.
    """
    s = _searcher({"fieldvalue:32885089": [_T("34382891", "a@b.com")],
                   '"32885089"': [_T("32885089"), _T("34382891", "a@b.com")]})
    got, tally = collect_tickets("32885089", s, lambda ts: "")
    assert [str(t.id) for t in got] == ["34382891"], [str(t.id) for t in got]
    assert tally["id_collision"] == 1, tally


def test_the_collision_is_reported_not_silently_dropped():
    """A ticket removed without a word is one nobody can check. If the
    exclusion is ever wrong, the count is how anyone finds out."""
    s = _searcher({'"1"': [_T("1")]})
    _, tally = collect_tickets("1", s, lambda ts: "")
    line = collect_trail("1", tally)
    assert line and "own NUMBER equals this booking id" in line["text"], line


def test_only_the_text_route_is_guarded():
    """`fieldvalue:` matched a CUSTOM FIELD — a statement about the booking —
    and a requester hit is about the person. Neither can collide this way, and
    excluding a real ticket because its number happens to match would lose the
    case's own ticket."""
    s = _searcher({"fieldvalue:34382891": [_T("34382891", "a@b.com")]})
    got, tally = collect_tickets("34382891", s, lambda ts: "")
    assert [str(t.id) for t in got] == ["34382891"]
    assert tally["id_collision"] == 0, tally


def test_a_collision_ticket_found_by_the_field_search_is_kept():
    """If the custom field says this ticket is about this booking, it is —
    whatever its own number happens to be."""
    s = _searcher({"fieldvalue:32885089": [_T("32885089")]})
    got, tally = collect_tickets("32885089", s, lambda ts: "")
    assert [str(t.id) for t in got] == ["32885089"]
    assert tally["id_collision"] == 0
