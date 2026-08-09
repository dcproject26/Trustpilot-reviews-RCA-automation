"""Step 12c actually runs, and says so when it does not.

`validate()` was called by nothing. Not "was not called for some inputs" —
never, on any run, for anyone. The line above the call named `draft`, which is
the SAVE step's local and is not bound until thirty lines further down, so
every process_review raised UnboundLocalError there; the surrounding
`except Exception` logged "RCA validation failed, keeping raw output" and the
model's raw JSON went to the screen. It is the first bullet of CLAUDE.md §1,
word for word, reintroduced: a validator wired into no path looks exactly like
one that works.

The same dead statement took a second disclosure with it. `import html as
_html` sat two lines BELOW the raising statement, so `_html` was never bound —
and the contact-note join, a separate try, then died on the NameError and
logged "contact-note join check skipped". The unmatched-`zd_ref` warning that
CLAUDE.md names as a canonical bug could fire and reach nobody.

Driven, because the strings are in the file either way. A source assertion for
`_validate_rca(` passes just as happily against the build that raised.
"""
import asyncio
import json
from datetime import datetime

import pytest

def _stub(monkeypatch, rca_v3, timeline=()):
    """Every outbound call replaced, so the run is offline and deterministic.

    server.pipeline is reloaded first: it binds SessionLocal at import, and
    live_db swaps the db module underneath it per test. Without the reload the
    second test in a file runs against the first test's deleted database and
    reports "Review not found" — a green-looking pass for a run that never
    happened, which is the failure this file is about.
    """
    import importlib
    import server.pipeline
    pipe = importlib.reload(server.pipeline)
    from server.services import (claude, zendesk, bigquery as bq, dss,
                                 slack as slk, rca_checklist as rcl)

    async def _none(*a, **k):
        return None

    async def _empty_dict(*a, **k):
        return {}

    async def _empty_list(*a, **k):
        return []

    monkeypatch.setattr(claude, "translate", _none)
    monkeypatch.setattr(claude, "stated_issue",
                        lambda *a, **k: _coro("The tickets were late."))
    monkeypatch.setattr(claude, "generate_rca_v2", _empty_dict)
    monkeypatch.setattr(claude, "generate_rca_v3",
                        lambda **k: _coro(json.loads(json.dumps(rca_v3))))
    monkeypatch.setattr(claude, "summarise_support_event",
                        lambda *a: _coro({"guestSaid": "", "weDid": "",
                                          "guestReply": "", "gap": ""}))
    monkeypatch.setattr(claude, "summarise_support_arc",
                        lambda *a: _coro("arc"))
    monkeypatch.setattr(claude, "extract_ticket_facts", _empty_dict)
    monkeypatch.setattr(claude, "analyze_wwr", _empty_list)
    monkeypatch.setattr(claude, "_call", lambda *a, **k: _coro(json.dumps(
        {"l1": "Operations Issue", "l2": "Ticket Issues",
         "sub_theme": "C. Ticket Delayed", "reasoning": "stub"})))
    monkeypatch.setattr(zendesk, "get_timeline",
                        lambda *a, **k: _coro((list(timeline), {},
                                               {"ticket_ids": [], "timeline_raw": [],
                                                "timeline_raw_ticket_ids": []})))
    monkeypatch.setattr(bq, "get_similar_complaints",
                        lambda *a, **k: _coro(([], [])))
    monkeypatch.setattr(bq, "get_l1_l2_by_bid", _empty_dict)
    monkeypatch.setattr(dss, "get_recommendation", _empty_dict)
    monkeypatch.setattr(slk, "search_mentions", _empty_list)
    monkeypatch.setattr(rcl, "get_checklist", _empty_list)
    monkeypatch.setattr(pipe, "get_canned_responses", _empty_list)


async def _coro_impl(v):
    return v


def _coro(v):
    return _coro_impl(v)


def _seed(db, rid, **kw):
    s = db.SessionLocal()
    s.add(db.Review(id=rid, slack_ts=rid, slack_channel="C1", rating=1,
                    author=kw.pop("author", "David Smith"),
                    body_original="the tickets were late",
                    body_english="the tickets were late",
                    status="new", received_at=datetime.utcnow(), **kw))
    s.commit()
    s.close()


def _run(db, rid):
    import sys
    pipe = sys.modules["server.pipeline"]
    asyncio.run(pipe.process_review(rid))
    s = db.SessionLocal()
    d = s.query(db.RcaDraft).filter_by(review_id=rid).first()
    assert d is not None, "the pipeline wrote no draft at all"
    out = (d.rca_v3 or {}, [e.get("text", "") for e in (d.confidence_trail or [])],
           d.actions_taken)
    s.close()
    return out


BASE = {
    "stated_issue": "the tickets were late",
    "tldr": {"our_mistake": "no disclosure", "our_fix": "refunded"},
    "l1": "Operations Issue", "l2": "Ticket Issues",
    "what_went_wrong": {"guest_issues": []},
    "flags": [], "area_of_improving": [], "booking_logs": [],
    "support_interaction_notes": [], "sp_interaction_notes": {},
    "sop_compliance": {}, "takedown": {"verdict": "No"}, "dss": {},
    "resolution": "refunded", "suggested_response": "We are sorry.",
}


def test_a_value_outside_the_enum_is_coerced_before_it_is_stored(live_db,
                                                                 monkeypatch):
    """claim_accuracy is a closed four-member vocabulary. The dashboard renders
    it as a chip-select, so an unknown member is an option that cannot be
    picked back off. If validate() runs, this comes back inside the list."""
    rca = json.loads(json.dumps(BASE))
    rca["what_went_wrong"]["guest_issues"] = [{
        "issue": "Delivery window not disclosed",
        "claim": "I was never told",
        "claim_accuracy": "TOTALLY TRUE",          # not a member
        "fix": {"action": "state the window", "owner": "CONTENT",
                "because": "the page is silent", "source": "exp-page"},
        "root_cause": "the page is silent",
        "evidence": [{"text": "no timeline on the page", "source": "exp-page",
                      "ref": None}]}]
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_enum")
    v3, trail, _ = _run(live_db, "tp_enum")

    got = v3["what_went_wrong"]["guest_issues"][0]["claim_accuracy"]
    assert got in ("Accurate", "Partly accurate", "Inaccurate", "Unknown"), \
        f"raw model output was stored unvalidated: {got!r}"


def test_a_coercion_is_announced_on_the_trail_not_only_in_the_log(live_db,
                                                                 monkeypatch):
    """A repair is 'we changed the model's answer', and the reader is on the
    card, not in the server log."""
    rca = json.loads(json.dumps(BASE))
    # Over the 120-word ceiling: validate() counts and says so.
    rca["suggested_response"] = " ".join(["sorry"] * 130)
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_note")
    _, trail, _ = _run(live_db, "tp_note")

    assert any("120-word ceiling" in t for t in trail), \
        f"no validation note reached the trail: {trail}"


def test_an_unmatched_zd_reference_is_reported_where_the_reader_is(live_db,
                                                                   monkeypatch):
    """CLAUDE.md's second bullet. The note joins to no frame; saying nothing
    is indistinguishable from a model that returned no notes."""
    rca = json.loads(json.dumps(BASE))
    rca["support_interaction_notes"] = [
        {"zd_ref": "ZD-99999", "summary": "a contact on no known ticket",
         "detail": None, "ce_miss": None}]
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_orphan")
    _, trail, _ = _run(live_db, "tp_orphan")

    assert any("ZD-99999" in t and "could not be joined" in t for t in trail), \
        f"the failed join was silent on the card: {trail}"


def test_validation_that_cannot_run_says_so_rather_than_looking_clean(
        live_db, monkeypatch):
    """The whole point. A validator that raised produced a card identical to
    one that passed — same green trail, same fields, no mark anywhere. If the
    call dies for any future reason the trail has to carry the fact."""
    rca = json.loads(json.dumps(BASE))
    _stub(monkeypatch, rca)

    import server.services.rca_v4_validate as V

    def _boom(*a, **k):
        raise RuntimeError("the validator is broken")

    monkeypatch.setattr(V, "validate", _boom)
    _seed(live_db, "tp_boom")
    _, trail, _ = _run(live_db, "tp_boom")

    assert any("validation did not run" in t.lower() for t in trail), \
        f"a dead validator left a clean-looking trail: {trail}"


def test_actions_taken_is_computed_by_the_run_not_left_empty(live_db,
                                                            monkeypatch):
    """actions_taken has exactly one writer — validate()'s
    actions_from_gaps, via project_v4. With validate() dead the column was
    projected from a key the model never emits, so every pipeline-generated
    review had an empty tab block: 'nothing was raised' rendered identically
    to 'the routing never ran'.

    The source is §3's fixes now, not six merged sources. The flag below still
    renders in Flags; it no longer also lands on a tab, which is the
    repetition the restructure removes.
    """
    from server.checklist import ACTION_TEAMS
    rca = json.loads(json.dumps(BASE))
    # The flag and the fix are both findings on this card and both name CO, so
    # CO's tab must carry them. Nothing here comes from the guideline sheet.
    rca["flags"] = [{"team": "CO",
                     "flag": "Tickets were not resent when the delay was seen",
                     "evidence": "no resend on the ticket", "zd_ref": None}]
    rca["what_went_wrong"]["guest_issues"] = [{
        "issue": "Tickets were sent late and never resent",
        "claim": "I was never told", "claim_accuracy": "Accurate",
        "root_cause": "nobody resent the tickets",
        "evidence": [{"text": "no resend", "source": "zendesk", "ref": None}]}]
    rca["what_went_wrong"]["gaps"] = [
        {"gap": "resend tickets when the delay is detected", "team": "CO",
         "source_ref": "ZD-1"}]
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_actions")
    _, trail, actions = _run(live_db, "tp_actions")

    assert isinstance(actions, dict), f"actions_taken is not a dict: {actions!r}"
    # The shape itself is the regression signal. With validate() dead the
    # projection read a key the model never emits and stored {} — nine missing
    # tabs render as nine empty ones.
    from server.checklist import ACTION_TAB_ORDER
    assert set(actions) == set(ACTION_TAB_ORDER), \
        (f"actions_taken is not the Unrouted + nine-team shape — the projection "
         f"read a key nothing wrote: {sorted(actions)}")
    assert actions["co"], \
        f"CO was flagged and owns the fix, and still nothing was raised: {actions}"
    # THE FINDINGS, not the playbook. A guideline row on this card would be a
    # statement about work nobody did.
    from server.checklist import _ALL_GUIDELINE_ACTIONS
    raised = {a for v in actions.values() for a in v}
    assert not (raised & _ALL_GUIDELINE_ACTIONS), \
        f"guideline rows reached the card: {raised & _ALL_GUIDELINE_ACTIONS}"
    assert any("resend" in a.lower() for a in actions["co"]), actions["co"]


def test_an_unroutable_finding_reaches_the_confidence_trail(live_db, monkeypatch):
    """An empty tab means several different things, so the run has to say
    which — and a finding that named no team is NOT on the card at all. A
    reader must not have to infer that from a section that looks complete.

    A fix that names no owner now lands on the UNROUTED tab — visible, rather
    than reported in a note under a tab strip that looks complete — and the
    run still says how many went there.
    """
    rca = json.loads(json.dumps(BASE))
    rca["flags"] = [
        {"team": "CO", "flag": "No follow-up", "evidence": "e", "zd_ref": None},
        {"team": "SP", "flag": "Vendor silent", "evidence": "e", "zd_ref": None}]
    rca["what_went_wrong"]["guest_issues"] = [{
        "issue": "Tickets were sent late",
        "claim": "I was never told", "claim_accuracy": "Accurate",
        "operational_failure": "Nobody watched the fulfilment queue",
        "root_cause": "nobody resent the tickets",
        "evidence": [{"text": "no resend", "source": "zendesk", "ref": None}]}]
    rca["what_went_wrong"]["gaps"] = [
        {"gap": "Someone should watch the fulfilment queue",
         "source_ref": "ZD-1"}]
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_unrouted")
    _, trail, actions = _run(live_db, "tp_unrouted")

    said = " ".join(t for t in trail if "actions taken" in t)
    assert "Unrouted tab" in said, trail
    assert "Someone should watch the fulfilment queue" in actions["unrouted"], (
        "an unowned fix must be visible on the Unrouted tab, not reported in a "
        "note under a tab strip that looks complete")
    for tab, rows in actions.items():
        if tab != "unrouted":
            assert "Someone should watch the fulfilment queue" not in rows, (
                f"an unowned fix was parked on {tab} — a row attributed to a "
                f"team that did nothing wrong")


# ── what a re-run may and may not destroy ───────────────────────────────────

def test_a_hand_typed_action_row_survives_a_pipeline_rerun(live_db, monkeypatch):
    """Actions Taken is recomputed from the guidelines and the flags on every
    run, so a row a person added is only kept by validate()'s `keep` door. The
    pipeline read the previous rows off `draft`, which is not bound yet — so
    the read raised, validate() never ran at all, and the row was gone."""
    from sqlalchemy.orm.attributes import flag_modified
    rca = json.loads(json.dumps(BASE))
    rca["flags"] = [{"team": "CO", "flag": "Tickets were not resent",
                     "evidence": "no resend on the ticket", "zd_ref": None}]
    _stub(monkeypatch, rca)
    _seed(live_db, "tp_hand")
    _run(live_db, "tp_hand")

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_hand").first()
    a = dict(d.actions_taken or {})
    a["co"] = list(a.get("co") or []) + ["Rang the guest personally at 18:40"]
    d.actions_taken = a
    flag_modified(d, "actions_taken")
    s.commit()
    s.close()

    _, trail, actions = _run(live_db, "tp_hand")
    assert "Rang the guest personally at 18:40" in (actions.get("co") or []), \
        f"the re-run destroyed the hand-typed row: {actions}"
    assert any("hand-added row" in t for t in trail), \
        "a row survived for a different reason from the rest and nothing says so"


def test_a_rerun_that_destroys_hand_edits_says_so_and_drops_the_marker(
        live_db, monkeypatch):
    """`rca_v3_edited_at` is what `_bulk_targets` and `tools/rerun_all.py` read
    to skip a review whose RCA a person wrote. A re-run replaces the blob whole
    and used to leave the marker standing, so the review was protected for ever
    on the strength of an edit that had already been thrown away — and nothing
    on the card said the edit had gone."""
    from sqlalchemy.orm.attributes import flag_modified
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_edited")
    _run(live_db, "tp_edited")

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_edited").first()
    blob = dict(d.rca_v3)
    blob["resolution"] = "TYPED BY A PERSON"
    d.rca_v3 = blob
    d.rca_v3_edited_at = datetime.utcnow()
    flag_modified(d, "rca_v3")
    s.commit()
    s.close()

    v3, trail, _ = _run(live_db, "tp_edited")
    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_edited").first()
    still_marked = d.rca_v3_edited_at
    s.close()

    assert v3.get("resolution") != "TYPED BY A PERSON", \
        "the fixture no longer exercises the case — the edit was not replaced"
    assert still_marked is None, \
        ("the edit was destroyed and rca_v3_edited_at is still set — every "
         "future bulk run will skip this review to protect work that is gone")
    assert any("hand-edited" in t for t in trail), \
        f"the re-run overwrote a person's wording in silence: {trail}"


def test_a_failed_generation_keeps_the_edit_and_its_marker(live_db, monkeypatch):
    """The inverse bug. Clearing the marker unconditionally would drop the
    protection from a draft whose blob was never replaced — the RCA call
    failed, the previous hand-edited blob is still there, and it is still the
    thing the marker describes."""
    from sqlalchemy.orm.attributes import flag_modified
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_kept")
    _run(live_db, "tp_kept")

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_kept").first()
    blob = dict(d.rca_v3)
    blob["resolution"] = "TYPED BY A PERSON"
    d.rca_v3 = blob
    d.rca_v3_edited_at = datetime.utcnow()
    flag_modified(d, "rca_v3")
    s.commit()
    s.close()

    # This run's RCA generation returns nothing, so the blob is not replaced.
    _stub(monkeypatch, {})
    v3, _, _ = _run(live_db, "tp_kept")
    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_kept").first()
    still_marked = d.rca_v3_edited_at
    s.close()

    assert v3.get("resolution") == "TYPED BY A PERSON", \
        "a failed generation wiped the previous RCA"
    assert still_marked is not None, \
        "the edit is still there and its protection marker was cleared anyway"


# ── an associate's confirmation is the strongest fact on the card ───────────

def test_a_confirmed_bid_survives_a_run_bigquery_could_not_be_asked_on(
        live_db, monkeypatch):
    """`select-candidate` stores the booking and then RE-RUNS the pipeline to
    fetch Zendesk, insights and the RCA for it. The confirmed-BID branch was
    gated on `is_live("bigquery")`, so with the warehouse unavailable the run
    fell past the confirmation into the "no real booking search was attempted"
    branch — whose save writes booking=None, candidates_list=[],
    candidate_state=False, match_tier=None.

    The associate's decision AND the shortlist they chose from were destroyed
    by the re-run their own click started, and the review came back reading
    Untraceable: identical to one nobody had ever looked at.

    Step 5a already states the principle for a booking id the GUEST typed. A
    person picking a booking off a shortlist is the stronger fact, and it was
    the one the floor did not cover.
    """
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_conf")

    import sys
    pipe = sys.modules["server.pipeline"]
    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(
        id="d_conf", review_id="tp_conf",
        booking={"id": "31246072", "experienceName": "Wieliczka Salt Mine"},
        selected_candidate_bid="31246072", match_tier=2,
        match_confidence="confirmed", candidate_state=False,
        candidates_list=[{"id": "31246072"}, {"id": "31246099"}],
        confidence_trail=[{"mark": "pass",
                           "text": "<strong>2 booking(s)</strong> match"}]))
    s.commit()
    s.close()

    # MOCK_MODE: is_live reports every service down, which is the condition.
    assert not pipe.is_live("bigquery"), "the fixture no longer exercises the case"
    asyncio.run(pipe.process_review("tp_conf"))

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_conf").first()
    booking = d.booking or {}
    trail = [e.get("text", "") for e in (d.confidence_trail or [])]
    from server.tiers import classify
    bucket = classify(d.review, d)
    s.close()

    assert booking.get("id") == "31246072", \
        f"the confirmed booking was erased by the re-run it triggered: {booking!r}"
    assert bucket == "identified", \
        f"a confirmed review came back in the {bucket!r} bucket"
    assert booking.get("_unverified") is True, \
        "the booking is presented as verified by a run that never asked BigQuery"
    assert any("was NOT" in t and "re-checked" in t for t in trail), \
        f"nothing says the confirmation was carried without being re-checked: {trail}"
    assert any("2 booking(s)" in t for t in trail), \
        "the run that produced the shortlist was erased from the trail"


def test_the_shortlist_behind_a_confirmation_is_not_thrown_away(live_db,
                                                                monkeypatch):
    """The candidate list is how an associate re-opens a decision they now
    doubt. Clearing it on a run that could not check anything removes the only
    record of what they chose between."""
    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_conf2")
    import sys
    pipe = sys.modules["server.pipeline"]
    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(
        id="d_conf2", review_id="tp_conf2",
        booking={"id": "31246072"}, selected_candidate_bid="31246072",
        match_tier=2, candidate_state=False,
        candidates_list=[{"id": "31246072"}, {"id": "31246099"}],
        confidence_trail=[]))
    s.commit()
    s.close()

    asyncio.run(pipe.process_review("tp_conf2"))
    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_conf2").first()
    cands = d.candidates_list or []
    cstate = d.candidate_state
    s.close()
    assert len(cands) == 2, f"the shortlist was discarded: {cands!r}"
    assert not cstate, "the picker was re-opened over a decision already made"


def test_the_shortlist_survives_a_confirmed_rerun_bigquery_did_verify(
        live_db, monkeypatch):
    """The same throw-away, on the branch where BigQuery IS available.

    Both confirmed-BID branches carried the shortlist only when the previous
    draft also had a TRAIL. A draft can hold candidates and no trail — an
    older build, or a run that stored the shortlist and died before writing
    the trail, which `server/tiers.classify` explicitly anticipates. The
    shortlist is the only record of what the associate chose between, so a
    gate on an unrelated field is what makes a confirmation impossible to
    revisit later.
    """
    import importlib
    import server.pipeline
    pipe = importlib.reload(server.pipeline)
    from server.services import (claude, zendesk, bigquery as bq, dss,
                                 slack as slk, rca_checklist as rcl,
                                 bigquery_patch as bqp, bigquery)

    async def _v(x):
        return x

    monkeypatch.setattr(pipe, "is_live", lambda s: s == "bigquery")
    monkeypatch.setattr(pipe, "MOCK_MODE", False)
    monkeypatch.setattr(claude, "translate", lambda *a, **k: _v(None))
    monkeypatch.setattr(claude, "stated_issue", lambda *a, **k: _v("x"))
    monkeypatch.setattr(claude, "generate_rca_v2", lambda *a, **k: _v({}))
    monkeypatch.setattr(claude, "generate_rca_v3", lambda **k: _v({}))
    monkeypatch.setattr(claude, "summarise_support_event", lambda *a: _v({}))
    monkeypatch.setattr(claude, "summarise_support_arc", lambda *a: _v(""))
    monkeypatch.setattr(claude, "extract_ticket_facts", lambda *a, **k: _v({}))
    monkeypatch.setattr(claude, "analyze_wwr", lambda *a, **k: _v([]))
    monkeypatch.setattr(claude, "_call", lambda *a, **k: _v("{}"))
    monkeypatch.setattr(bqp, "verify_bid",
                        lambda b: {"id": str(b), "experienceName": "X"})
    monkeypatch.setattr(bigquery, "_get_booking_extra", lambda b: {})
    monkeypatch.setattr(bq, "get_similar_complaints", lambda *a, **k: _v(([], [])))
    monkeypatch.setattr(bq, "get_l1_l2_by_bid", lambda *a, **k: _v({}))
    monkeypatch.setattr(zendesk, "get_timeline", lambda *a, **k: _v(
        ([], {}, {"ticket_ids": [], "timeline_raw": [],
                  "timeline_raw_ticket_ids": []})))
    monkeypatch.setattr(slk, "search_mentions", lambda *a, **k: _v([]))
    monkeypatch.setattr(dss, "get_recommendation", lambda *a, **k: _v({}))
    monkeypatch.setattr(rcl, "get_checklist", lambda *a, **k: _v([]))
    monkeypatch.setattr(pipe, "get_canned_responses", lambda *a, **k: _v([]))

    _seed(live_db, "tp_cl")
    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(
        id="d_cl", review_id="tp_cl", booking={"id": "31246072"},
        selected_candidate_bid="31246072", match_tier=2, candidate_state=False,
        candidates_list=[{"id": "31246072"}, {"id": "31246099"}],
        confidence_trail=[]))          # candidates, and no trail
    s.commit()
    s.close()

    asyncio.run(pipe.process_review("tp_cl"))
    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_cl").first()
    cands = d.candidates_list or []
    bid = (d.booking or {}).get("id")
    s.close()
    assert bid == "31246072", "the verified confirmation was lost"
    assert len(cands) == 2, f"the shortlist was discarded: {cands!r}"
