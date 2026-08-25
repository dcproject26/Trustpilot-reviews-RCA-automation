"""A ticket we FOUND and could not READ is a finding, not a log line.

THE BUG. _get_timeline_sync fetched each ticket's comments and, on failure,
did `log.warning(...); continue`. The ticket was not a prior trip and not
another booking's, so it stayed in meta["ticket_ids"] — the card counted it as
found — while not one word of it reached the timeline. "We could not open this
conversation" and "this conversation is empty" produced identical output.

WHY IT IS WORSE THAN A MISSING LINE. The RCA is written FROM this timeline. A
guest writes "I reached out to the help chat and they confirmed the tickets
were available"; the chat ticket is found and its fetch fails; the model, shown
a timeline without it, reports that the contact is not on record. An absence
asserted on the strength of a lookup that failed — the first rule of CLAUDE.md,
in the sentence a CX associate acts on.

Three absences, three different words, and the third is the only one that is
our fault:
    prior trip           we looked and decided: it predates the booking
    another booking      we looked and decided: its field names another
    UNREADABLE           we did not look, because we could not
"""
import pytest

from server.services import zendesk as Z
from tests.test_prior_trip_tickets import _Cmt, _Ticket, _Client, _wire


class _BrokenClient(_Client):
    """Comments raise for one nominated ticket, as a 404/500/timeout does."""
    def __init__(self, tickets, breaks_on, exc=None):
        super().__init__(tickets)
        self._breaks_on = str(breaks_on)
        self._exc = exc or RuntimeError("RecordNotFound: no such ticket")

    def comments(self, ticket=None):
        if str(ticket) == self._breaks_on:
            raise self._exc
        return super().comments(ticket=ticket)


def _run(monkeypatch, tickets, breaks_on, booked_on="2026-08-01T00:00:00Z", exc=None):
    _wire(monkeypatch, tickets)
    client = _BrokenClient(tickets, breaks_on, exc)
    return Z._get_timeline_sync(client, "33543686", booked_on=booked_on)


def _pair():
    chat = _Ticket("34807804", [_Cmt("2026-08-19T02:22:00Z", "the help chat")])
    call = _Ticket("34807896", [_Cmt("2026-08-19T02:32:00Z", "the 31 May call")])
    return chat, call


# ── it is reported, with the reason ─────────────────────────────────────────

def test_an_unreadable_ticket_is_reported_not_only_logged(monkeypatch):
    chat, call = _pair()
    _raw, _ex, meta = _run(monkeypatch, [chat, call], breaks_on="34807804")
    got = meta.get("unreadable_tickets")
    assert got, "a ticket that could not be read vanished silently"
    assert got[0]["ticket_id"] == "34807804"


def test_the_reason_is_carried_so_it_can_be_acted_on(monkeypatch):
    """"Could not be read" with no cause sends nobody anywhere. A 404 is a
    deleted ticket, a 403 is a permissions problem, a timeout is retryable."""
    chat, call = _pair()
    _raw, _ex, meta = _run(monkeypatch, [chat, call], breaks_on="34807804",
                           exc=RuntimeError("APIException: 403 Forbidden"))
    assert "403" in meta["unreadable_tickets"][0]["error"]


def test_a_readable_run_reports_an_empty_list_not_a_missing_key(monkeypatch):
    """Checked-and-clean must be a value a caller can read, not the absence of
    a key — otherwise "nothing failed" and "this build has no such check" are
    the same `.get()` returning None."""
    chat, call = _pair()
    _wire(monkeypatch, [chat, call])
    _raw, _ex, meta = Z._get_timeline_sync(_Client([chat, call]), "33543686",
                                           booked_on="2026-08-01T00:00:00Z")
    assert meta["unreadable_tickets"] == []


# ── it is not counted as a ticket the timeline covers ───────────────────────

def test_an_unreadable_ticket_is_not_counted_as_one_we_read(monkeypatch):
    """THE HEADLINE DEFECT. It stayed in ticket_ids, so "4 tickets" printed
    over a timeline built from 3."""
    chat, call = _pair()
    _raw, _ex, meta = _run(monkeypatch, [chat, call], breaks_on="34807804")
    assert "34807804" not in meta["ticket_ids"], (
        "an unreadable ticket is still counted as covered by the timeline")
    assert "34807896" in meta["ticket_ids"], "the readable one went missing too"


def test_the_readable_tickets_still_build_a_timeline(monkeypatch):
    """One broken fetch must not cost the whole timeline."""
    chat, call = _pair()
    raw, _ex, _meta = _run(monkeypatch, [chat, call], breaks_on="34807804")
    assert {e["ticket_id"] for e in raw} == {"34807896"}


def test_a_rate_limit_is_not_reported_as_an_unreadable_ticket(monkeypatch):
    """A 429 means "ask again more slowly", not "this ticket is unreadable".
    Filing it as the latter would send someone to look at a healthy ticket,
    and re-running — the advice for an unreadable one — makes a rate limit
    worse."""
    chat, call = _pair()
    with pytest.raises(Z.ZendeskRateLimited):
        _run(monkeypatch, [chat, call], breaks_on="34807804",
             exc=Z.ZendeskRateLimited("rate limited, retry in 60s"))


# ── the three absences stay three different findings ────────────────────────

def test_unreadable_is_kept_apart_from_the_two_deliberate_exclusions(monkeypatch):
    """A decision and a failure must not share a list. One says the timeline
    is correct; the other says it is incomplete."""
    chat, call = _pair()
    _raw, _ex, meta = _run(monkeypatch, [chat, call], breaks_on="34807804")
    assert meta["prior_trip_excluded"] == []
    assert meta["other_booking_excluded"] == []
    assert len(meta["unreadable_tickets"]) == 1


def test_a_prior_trip_that_is_also_unreadable_is_never_fetched(monkeypatch):
    """The date filter needs comments, but the other-booking filter does not —
    and a ticket excluded before the fetch cannot be an unreadable one. It
    would be a fetch we chose not to make, reported as one that failed."""
    old = _Ticket("33535069", [_Cmt("2026-07-06T17:42:00Z", "an earlier trip")])
    old.custom_fields = []
    monkeypatch.setattr(Z, "booking_id_from_ticket", lambda t: "32358051")
    chat, _call = _pair()
    _raw, _ex, meta = _run(monkeypatch, [old, chat], breaks_on="33535069")
    assert meta["unreadable_tickets"] == [], \
        "a ticket excluded before the fetch was reported as unreadable"


# ── the wiring, which is the half that goes missing ─────────────────────────

def test_the_finding_reaches_the_confidence_trail_as_a_failure():
    """`fail`, not `pass`. The other two exclusions are `pass` because they
    report the filter WORKING. This one reports missing evidence, and a gap in
    the record shown as a step that succeeded is the specific miswording
    CLAUDE.md calls out.

    Driven, not searched for in the source: `assert \'"mark": "fail"\' in
    pipeline_source` passes just as happily against a build where that line
    sits behind a condition that is never true."""
    from server.pipeline import unreadable_tickets_entry
    row = unreadable_tickets_entry(
        {"unreadable_tickets": [{"ticket_id": "34807804",
                                 "error": "APIException: 403 Forbidden"}]})
    assert row["mark"] == "fail", row
    assert "34807804" in row["text"]
    assert "403" in row["text"], "the cause is dropped from the line a human reads"


def test_a_clean_timeline_adds_no_line_at_all():
    """The counterpart. A trail that says "0 tickets were unreadable" on every
    healthy run is a line nobody reads by the third card."""
    from server.pipeline import unreadable_tickets_entry
    assert unreadable_tickets_entry({"unreadable_tickets": []}) is None
    assert unreadable_tickets_entry({}) is None
    assert unreadable_tickets_entry(None) is None


def test_the_line_says_the_rca_was_written_without_them():
    """The consequence is the point. "A ticket could not be read" is a fact
    about Zendesk; "the finding below was written without it" is what tells
    the associate not to trust an absence on this card."""
    from server.pipeline import unreadable_tickets_entry
    txt = unreadable_tickets_entry(
        {"unreadable_tickets": [{"ticket_id": "1", "error": "boom"}]})["text"]
    assert "written without" in txt, txt
    assert "not the same as a ticket with nothing in it" in txt, txt


def test_more_unreadable_than_shown_are_counted_not_dropped():
    from server.pipeline import unreadable_tickets_entry
    rows = [{"ticket_id": str(i), "error": "boom"} for i in range(7)]
    txt = unreadable_tickets_entry({"unreadable_tickets": rows})["text"]
    assert "7 ticket(s)" in txt, txt
    assert "and 3 more" in txt, txt


def test_a_missing_reason_is_named_rather_than_rendered_as_none():
    """"(None)" in a sentence a human reads is our storage leaking again."""
    from server.pipeline import unreadable_tickets_entry
    txt = unreadable_tickets_entry(
        {"unreadable_tickets": [{"ticket_id": "1"}]})["text"]
    assert "None" not in txt, txt
    assert "no reason recorded" in txt, txt


# ── the APPEND, driven through the pipeline ─────────────────────────────────
# A mutation deleting `confidence_trail.append(_ur_row)` and leaving the row
# built survived every test above: unreadable_tickets_entry was thoroughly
# driven and the line that USED it was not. That is the same shape as a
# validator wired into no path — the unit is green and the card is unchanged —
# and it is the failure CLAUDE.md opens with.

import asyncio
import importlib
from datetime import datetime


def _seed_review(db, rid="tp_unread"):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="9.9", slack_channel="C_ORM",
                        rating=1, author="Jimmy",
                        body_original="the guide never showed up",
                        body_english="the guide never showed up",
                        reference_number="33543686", status="new",
                        received_at=datetime(2026, 8, 22)))
        s.commit()
    finally:
        s.close()
    return rid


def _trail_of(db, rid):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        return list((d.confidence_trail if d else None) or [])
    finally:
        s.close()


def _run_with_meta(live_db, monkeypatch, meta, rid):
    import server.pipeline as P
    importlib.reload(P)
    monkeypatch.setattr(P, "is_live", lambda svc: svc in ("bigquery", "zendesk"))
    monkeypatch.setattr(P, "MOCK_MODE", False)

    import server.services.bigquery_patch as bqp
    monkeypatch.setattr(bqp, "verify_bid", lambda bid: {
        "id": "33543686", "date_of_booking": "2026-08-01 00:00:00+00:00",
        "experienceName": "Colosseum Tour"})
    import server.services.bigquery as BQ
    monkeypatch.setattr(BQ, "_get_booking_extra", lambda bid: {})

    async def _timeline(bid, review_id, **kw):
        return [], {}, meta
    monkeypatch.setattr(P.zendesk, "get_timeline", _timeline)

    async def _noop_dss(booking, review_id="", **kw):
        return {}
    monkeypatch.setattr(P.dss, "get_recommendation", _noop_dss)

    _seed_review(live_db, rid)
    try:
        asyncio.run(P.process_review(rid))
    except Exception:
        pass          # later steps need a model; the trail is written before them
    return _trail_of(live_db, rid)


def test_the_unreadable_line_actually_lands_on_the_card(live_db, monkeypatch):
    trail = _run_with_meta(live_db, monkeypatch, {
        "ticket_ids": ["34807896"], "timeline_raw": [],
        "unreadable_tickets": [{"ticket_id": "34807804",
                                "error": "APIException: 403 Forbidden"}],
    }, "tp_unread_yes")
    hits = [r for r in trail if "could NOT be read" in str(r.get("text", ""))]
    assert hits, (
        "the finding never reached the confidence trail — the row is built and "
        f"dropped. Trail was: {[r.get('text', '')[:60] for r in trail]}")
    assert hits[0]["mark"] == "fail", hits[0]
    assert "34807804" in hits[0]["text"]


def test_a_clean_timeline_leaves_no_such_line_on_the_card(live_db, monkeypatch):
    """The counterpart: it must not appear on every run."""
    trail = _run_with_meta(live_db, monkeypatch, {
        "ticket_ids": ["34807896"], "timeline_raw": [],
        "unreadable_tickets": [],
    }, "tp_unread_no")
    assert not [r for r in trail if "could NOT be read" in str(r.get("text", ""))], \
        "a clean run reported unreadable tickets"
