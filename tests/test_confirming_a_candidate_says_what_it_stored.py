"""Confirming a candidate whose booking record cannot be read.

`select_candidate` stores the FULL BigQuery row rather than the candidate
dict, because a candidate carries only what the picker draws. When that lookup
came back empty the endpoint did this:

    d.booking = {**match, **(full or {}), "id": body.bid} if full else match
    if not full:
        log.warning(...)

and that was the whole of it. On the shortlist path `match` was an id and a
row of empty strings, so the stored booking was an id and nothing else.
`classify()` reads `booking["id"]`, files the review under IDENTIFIED, and the
card draws a confirmed match with no experience, no date and no vendor. The
only record that anything had gone wrong was a line in a log which, as the
client's own comment says two panels away, "the people reading these cards do
not read".

So the endpoint now records WHICH of three things happened, on the draft:

    found   the warehouse answered and its fields are on the booking
    absent  the warehouse has no booking with this id
    failed  the lookup did not complete

`absent` and `failed` are kept apart here for the same reason as everywhere
else in this codebase: the first is a dead end an associate acts on by asking
the guest for a reference, the second is a re-run.
"""
import asyncio
from datetime import datetime

import pytest


CAND = {"id": "32885089", "experience": "", "experienceName": "",
        "date_of_visit": "", "vendorName": "", "primary_guest_name": "",
        "matchReasons": ["name"], "narrowing_path": "indicator_shortlist"}

FULL = {"id": "32885089", "experienceName": "Eiffel Tower Summit",
        "date_of_visit": "2026-08-04", "vendorName": "Acme Tours",
        "tid": "1", "tgid": "2", "vid": "3"}


def _seed(db, candidates=None):
    s = db.SessionLocal()
    s.add(db.Review(id="tp_x", slack_ts="9.0", slack_channel="C_MOCK_ORM",
                    rating=2, author="Bhayani Salim", language="en",
                    body_original="late tickets", body_english="late tickets",
                    status="draft", received_at=datetime(2026, 8, 1)))
    s.add(db.RcaDraft(id="draft_tp_x", review_id="tp_x",
                      candidate_state=True,
                      candidates_list=candidates if candidates is not None else [dict(CAND)],
                      confidence_trail=[{"mark": "pass", "text": "seeded"}]))
    s.commit()
    s.close()


def _stored(db):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == "tp_x").first()
        return d.booking or {}, list(d.confidence_trail or [])
    finally:
        s.close()


def _confirm(bid="32885089"):
    import server.api as api
    import server.db as db
    from fastapi import BackgroundTasks
    s = db.SessionLocal()
    try:
        return asyncio.run(api.select_candidate(
            "tp_x", api.CandidateSelect(bid=bid), BackgroundTasks(), s))
    finally:
        s.close()


@pytest.fixture()
def api_db(live_db):
    # No module reload — see the note in test_apply_english_reply.py.
    return live_db


@pytest.fixture()
def warehouse(monkeypatch):
    """Install what verify_bid does, and record that it was called."""
    calls = []

    def _install(answer):
        import server.services.bigquery_patch as bp

        def _v(bid):
            calls.append(bid)
            if isinstance(answer, Exception):
                raise answer
            return answer
        monkeypatch.setattr(bp, "verify_bid", _v)
        return calls
    return _install


# ── the good path is unchanged ─────────────────────────────────────────────

def test_a_readable_booking_is_stored_whole_and_marked_found(api_db, warehouse):
    """The warehouse row wins — a candidate carries only what the picker draws,
    and date_of_booking, fulfilment type and booking status are never in it."""
    warehouse(dict(FULL))
    _seed(api_db)
    _confirm()
    booking, _ = _stored(api_db)
    assert booking["experienceName"] == "Eiffel Tower Summit"
    assert booking["tgid"] == "2"
    assert booking["details_lookup"] == "found"


def test_a_readable_booking_adds_no_warning_to_the_trail(api_db, warehouse):
    """A warning on every healthy confirmation is the noise that makes a
    reader stop reading the trail at all."""
    warehouse(dict(FULL))
    _seed(api_db)
    _confirm()
    _, trail = _stored(api_db)
    assert not [t for t in trail if "was not read" in t.get("text", "")], trail


# ── the two failure paths, which must not look alike ───────────────────────

def test_a_missing_booking_is_recorded_on_the_draft_not_only_the_log(api_db, warehouse):
    warehouse(None)
    _seed(api_db)
    _confirm()
    booking, _ = _stored(api_db)
    assert booking["details_lookup"] == "absent"


def test_a_missing_booking_says_so_on_the_trail(api_db, warehouse):
    warehouse(None)
    _seed(api_db)
    _confirm()
    _, trail = _stored(api_db)
    line = [t for t in trail if "was not read" in t.get("text", "")]
    assert line, trail
    assert line[0]["mark"] == "warn", line
    assert "no booking with this id" in line[0]["text"], line
    assert "Check the id" in line[0]["text"], line


def test_a_lookup_that_did_not_complete_is_not_called_a_missing_booking(api_db, warehouse):
    """THE PAIR THAT MUST NOT COLLAPSE. One sends someone to ask the guest for
    a reference; the other sends them to press Re-run."""
    warehouse(RuntimeError("bigquery is down"))
    _seed(api_db)
    _confirm()
    booking, trail = _stored(api_db)
    assert booking["details_lookup"] == "failed"
    line = [t for t in trail if "was not read" in t.get("text", "")]
    assert line, trail
    assert "did not complete" in line[0]["text"], line
    assert "re-run" in line[0]["text"].lower(), line
    assert "no booking with this id" not in line[0]["text"], line


def test_the_two_failures_do_not_share_a_sentence(api_db, warehouse, monkeypatch):
    warehouse(None)
    _seed(api_db)
    _confirm()
    absent = [t["text"] for t in _stored(api_db)[1] if "was not read" in t["text"]][0]

    import server.services.bigquery_patch as bp

    def _boom(bid):
        raise RuntimeError("down")
    monkeypatch.setattr(bp, "verify_bid", _boom)
    s = api_db.SessionLocal()
    d = s.query(api_db.RcaDraft).filter(api_db.RcaDraft.review_id == "tp_x").first()
    d.candidate_state = True
    d.confidence_trail = [{"mark": "pass", "text": "seeded"}]
    s.commit()
    s.close()
    _confirm()
    failed = [t["text"] for t in _stored(api_db)[1] if "was not read" in t["text"]][0]
    assert absent != failed, absent


# ── what is stored either way ──────────────────────────────────────────────

def test_the_confirmation_still_happens_when_the_lookup_fails(api_db, warehouse):
    """The associate chose this booking. Refusing the confirmation because the
    warehouse is unreachable would take away the one action available on a
    card whose whole problem is that the warehouse is unreachable."""
    warehouse(None)
    _seed(api_db)
    out = _confirm()
    assert out["ok"] is True
    s = api_db.SessionLocal()
    try:
        d = s.query(api_db.RcaDraft).filter(api_db.RcaDraft.review_id == "tp_x").first()
        assert d.selected_candidate_bid == "32885089"
        assert d.candidate_state is False
    finally:
        s.close()


def test_the_candidates_own_fields_are_kept_when_the_lookup_fails(api_db, warehouse):
    """They are all the reader has. Blanking them to signal the failure would
    throw away the ticket's answer to make room for saying we have no answer."""
    warehouse(None)
    _seed(api_db, candidates=[dict(CAND, experienceName="From the ticket",
                                   experience="From the ticket")])
    _confirm()
    booking, _ = _stored(api_db)
    assert booking["experienceName"] == "From the ticket"


def test_the_stored_booking_is_not_the_candidate_object_itself(api_db, warehouse):
    """`d.booking = match` handed the draft a reference to a dict still inside
    `candidates_list`, so a later write to one silently edited the other."""
    warehouse(None)
    _seed(api_db)
    _confirm()
    s = api_db.SessionLocal()
    try:
        d = s.query(api_db.RcaDraft).filter(api_db.RcaDraft.review_id == "tp_x").first()
        cand = [c for c in d.candidates_list if c["id"] == "32885089"][0]
        assert "details_lookup" not in cand, \
            "confirming wrote through into the candidates list"
    finally:
        s.close()
