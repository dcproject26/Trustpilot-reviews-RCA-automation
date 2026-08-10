"""The Zendesk trace tells the four empty-record causes apart.

WHY IT EXISTS. A card reading "No direct interaction found between the
customer and the support team" is the model correctly describing an empty
record, and it cannot say which of these emptied it:

  nothing indexed under this booking id
  the one hit was a TICKET numbered like the BOOKING and was dropped
  the requester route never ran, because it needs an email it reads off the
    tickets the first two routes found
  the tickets were found and lost later, in shaping or the contact split

Same sentence on the card for all four, four different fixes. These drive
`main()` with the real `collect_tickets` underneath and a fake search, so what
is tested is the script, not a description of it.
"""
import pytest

from scripts import trace_zendesk as T


class Tk:
    def __init__(self, tid, subject="s", email=""):
        self.id = tid
        self.subject = subject
        self.requester = type("R", (), {"email": email})()


def _run(monkeypatch, capsys, rows_by_query, live=True):
    import server.config as cfg
    from server.services import zendesk as Z
    monkeypatch.setattr(cfg, "is_live", lambda svc: live)
    monkeypatch.setattr(Z, "_get_client", lambda: object())
    monkeypatch.setattr(Z, "_search_with_retry",
                        lambda _z, q: rows_by_query.get(q, []))
    rc = T.main(["32728059"])
    return rc, capsys.readouterr().out


def test_a_dead_zendesk_refuses_to_print_empty_routes(monkeypatch, capsys):
    """Every route would return [] and read exactly like a real miss. The
    whole point of the script is telling those apart, so it must not run."""
    rc, out = _run(monkeypatch, capsys, {}, live=False)
    assert rc == 1
    assert "NOTHING BELOW WOULD MEAN ANYTHING" in out


def test_the_id_collision_drop_is_named_not_silent(monkeypatch, capsys):
    """THE CASE THAT STARTED THIS. Ticket #32728059 is a real, unrelated
    ticket whose NUMBER equals booking 32728059. Dropping it is correct.
    Dropping it silently leaves a zero that looks like nothing was indexed."""
    rc, out = _run(monkeypatch, capsys, {
        'type:ticket "32728059"': [Tk("32728059", "Tickets for Wicked")],
    })
    assert rc == 0
    assert "dropped as id collision  1" in out
    assert "TICKET numbered like this BOOKING" in out


def test_the_skipped_requester_route_says_it_did_not_run(monkeypatch, capsys):
    """The consequence, spelled out: it is a gap in the cascade, not a guest
    who never wrote in. Those are opposite conclusions from the same zero."""
    rc, out = _run(monkeypatch, capsys, {
        'type:ticket "32728059"': [Tk("32728059", "unrelated")],
    })
    assert "THE REQUESTER SEARCH DID NOT RUN" in out
    assert "invisible to this" in out
    assert "not a guest who never wrote in" in out


def test_a_route_that_ran_and_found_nothing_is_not_called_skipped(monkeypatch,
                                                                  capsys):
    """The converse, and the reason the distinction is worth printing. Here
    the first routes DID find a ticket with an email, so the requester search
    ran — a zero from it means something different."""
    rc, out = _run(monkeypatch, capsys, {
        "type:ticket fieldvalue:32728059": [Tk("111", "real", "g@x.com")],
        "type:ticket requester:g@x.com": [],
    })
    assert "THE REQUESTER SEARCH DID NOT RUN" not in out
    assert "by the same requester    0" in out


def test_tickets_that_reach_shaping_point_at_the_other_trace(monkeypatch,
                                                             capsys):
    """If they got this far and the card still shows nothing, the loss is
    downstream and this script must say so rather than imply it looked."""
    rc, out = _run(monkeypatch, capsys, {
        "type:ticket fieldvalue:32728059": [Tk("111", "real", "g@x.com")],
        "type:ticket requester:g@x.com": [Tk("222", "another", "g@x.com")],
    })
    assert "TOTAL 2 ticket(s) reached shaping" in out
    assert "trace_contacts.py" in out
    assert "No direct\n  interaction found" not in out, \
        "it blamed the search on a run where the search worked"


def test_an_empty_result_names_both_symptoms_it_causes(monkeypatch, capsys):
    """The chain, said once: an empty record is what makes the card report no
    contact AND what makes the timeline narrate the review instead of events.
    Reading them as two unrelated bugs cost a session."""
    rc, out = _run(monkeypatch, capsys, {})
    assert "falling back" in out or "fall back" in out
    assert "timeline" in out


def test_every_query_that_went_out_is_printed(monkeypatch, capsys):
    """A route reported as 0 without its query shown cannot be checked against
    Zendesk by hand, which is the only way to tell a wrong query from a real
    absence."""
    rc, out = _run(monkeypatch, capsys, {
        "type:ticket fieldvalue:32728059": [Tk("111", "real", "g@x.com")],
    })
    assert "QUERY  type:ticket fieldvalue:32728059" in out
    assert 'QUERY  type:ticket "32728059"' in out
    assert "QUERY  type:ticket requester:g@x.com" in out
