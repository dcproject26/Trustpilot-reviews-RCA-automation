"""The confidence trail must describe the card it sits on.

WHAT IT WAS DOING. On a confirmed-BID re-run the WHOLE previous trail was
carried forward and this run appended to it. So a card with 20 timeline rows,
12 dated case findings and 2 routed gaps still carried, from the run before
the booking was confirmed:

    "Zendesk was not searched — the empty events timeline is a lookup that
     never ran"
    "The reply is an approved macro — no booking was matched"
    "4 of 4 case finding(s) carry no time"
    "actions taken: no unsolved gap was found in this case"

Every one of them false of the card it was on. Someone opening the trail to
find out why a section looked thin was told the lookup never ran, on a run
that found four tickets and shaped twenty events from them.

That is the first rule in CLAUDE.md inverted. The usual failure is a broken
mechanism reading as an empty result; this is a healthy run wearing a broken
run's report, and it is worse, because the trail is the one place a reader
goes to check.

THE MATCHING STEPS STAY. They are why a human had to confirm the booking, they
are not re-derived on a re-run, and losing them makes the confirmation
impossible to revisit — which is what carrying the trail forward was FOR.

POSITIONAL, NOT A CLASSIFICATION. The prior trail is written in order —
matching, Zendesk, RCA, reply — so only the FIRST re-derived entry has to be
recognised and everything after it goes with it. An RCA-phase line added later
is dropped without anyone remembering to list it.
"""
from server.pipeline import (matching_history, superseded_trail_row,
                             _REDERIVED_LEADS)


def _row(text, mark="pass"):
    return {"mark": mark, "text": text}


# The trail from the real card, in order, trimmed to the shape that matters.
MATCHING = [
    _row("<strong>BID</strong> — no 7–12 digit number found", "fail"),
    _row("<strong>Extracted from review:</strong> venue='—' · city='—'"),
    _row("<strong>Author parsed:</strong> first='Roisin' last='Sheehy'"),
    _row("<strong>The ticket carries no guest name</strong>, so the name was "
         "not verified", "warn"),
    _row("<strong>1 booking(s)</strong> match the indicators from this review"),
    _row("<strong>Result:</strong> 1 possible match(es) — pick one to continue"),
]
SUPERSEDED = [
    _row("<strong>Zendesk was not searched</strong> — this review has no "
         "booking id, so the empty events timeline is a lookup that never ran",
         "warn"),
    _row("<strong>The reply is an approved macro, sent as written</strong> — "
         "no booking was matched to this review"),
    _row("<strong>RCA</strong> — 4 of 4 case finding(s) carry no time", "warn"),
    _row("<strong>RCA</strong> — actions taken: no unsolved gap was found in "
         "this case", "warn"),
]


def test_the_matching_steps_are_kept():
    kept, _ = matching_history(MATCHING + SUPERSEDED)
    assert kept == MATCHING, [r["text"][:40] for r in kept]


def test_the_superseded_steps_are_cut():
    kept, cut = matching_history(MATCHING + SUPERSEDED)
    assert cut == len(SUPERSEDED)
    said = " ".join(r["text"] for r in kept)
    assert "lookup that never ran" not in said
    assert "no unsolved gap was found" not in said


def test_everything_after_the_boundary_goes_with_it():
    """POSITIONAL. A line type nobody listed still gets dropped, because it
    sits after the first re-derived entry — which is the whole reason this is
    a cut and not a filter."""
    unknown = _row("<strong>Some future step</strong> — added next quarter")
    kept, cut = matching_history(MATCHING + SUPERSEDED + [unknown])
    assert kept == MATCHING
    assert cut == len(SUPERSEDED) + 1


def test_a_trail_with_no_superseded_steps_is_untouched():
    """The first run, confirmed before any Zendesk work. Cutting anything here
    would delete history for nothing."""
    kept, cut = matching_history(MATCHING)
    assert kept == MATCHING and cut == 0


def test_an_empty_trail_is_not_an_error():
    assert matching_history([]) == ([], 0)
    assert matching_history(None) == ([], 0)


def test_a_non_dict_row_does_not_break_the_cut():
    kept, _ = matching_history(["a string", *MATCHING, *SUPERSEDED])
    assert kept == MATCHING


def test_the_markup_does_not_hide_the_lead_in():
    """Every trail line opens with a <strong> tag. Matching on the raw string
    would find none of them and cut nothing — the silent version of this bug."""
    for lead in _REDERIVED_LEADS:
        kept, cut = matching_history(
            MATCHING + [_row(f"<strong>{lead}</strong> — something")])
        assert cut == 1, f"{lead!r} was not recognised through its markup"
        assert kept == MATCHING


# ── the cut announces itself ───────────────────────────────────────────────

def test_the_cut_is_reported_in_words():
    """A trail that quietly shrinks is indistinguishable from a run that
    recorded less."""
    row = superseded_trail_row(4)
    assert row and "4 step(s) from the earlier run were removed" in row["text"]
    assert "matching steps above are kept" in row["text"]


def test_nothing_cut_says_nothing():
    """A line on every clean re-run is the noise that stops the trail being
    read."""
    assert superseded_trail_row(0) is None


# ── driven through the pipeline's own carry-forward ────────────────────────

def test_a_confirmed_rerun_carries_matching_and_drops_the_rest(live_db,
                                                               monkeypatch):
    """THE WIRING, DRIVEN THROUGH process_review.

    A first version called `matching_history` directly on a seeded draft and
    asserted the result — which is a test of the function, not of the line
    that uses it. Replacing the call site with `list(_prior.confidence_trail)`
    SURVIVED the whole suite. That is the exact shape `record_validation`'s
    docstring warns about, three files over, and it caught me anyway.

    This runs the pipeline the way `select-candidate` does: a confirmed BID on
    a draft whose trail carries a previous run's Zendesk and RCA lines."""
    import asyncio
    import json
    import sys
    from tests.test_pipeline_validates_its_rca import _stub, _seed, BASE

    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_tr")
    pipe = sys.modules["server.pipeline"]

    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(
        id="d_tp_tr", review_id="tp_tr",
        booking={"id": "31246072"}, selected_candidate_bid="31246072",
        match_tier=2, match_confidence="confirmed", candidate_state=False,
        confidence_trail=MATCHING + SUPERSEDED))
    s.commit(); s.close()

    asyncio.run(pipe.process_review("tp_tr"))

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_tr").first()
    said = " ".join(e.get("text", "") for e in (d.confidence_trail or []))
    s.close()

    assert "Author parsed" in said, \
        "the matching history was lost — that is what the carry-forward is for"
    assert "lookup that never ran" not in said, \
        "a superseded Zendesk line survived onto a card it is not true of"
    assert "4 of 4 case finding(s) carry no time" not in said, \
        "a superseded RCA line survived the carry"
    assert "step(s) from the earlier run were removed" in said, \
        "the cut happened and nothing said so"


def test_the_bigquery_live_branch_cuts_too(live_db, monkeypatch):
    """THE OTHER CALL SITE. There are two carry-forward branches — one when
    the warehouse can verify the confirmed BID, one when it cannot — and the
    test above only reaches the second, because MOCK_MODE reports every
    service down. Mutating the first survived the whole suite: covered in
    appearance, unexecuted in fact.

    `is_live` and `verify_bid` are stubbed so the run takes the branch it
    otherwise never takes here."""
    import asyncio
    import json
    import sys
    from tests.test_pipeline_validates_its_rca import _stub, _seed, BASE

    _stub(monkeypatch, json.loads(json.dumps(BASE)))
    _seed(live_db, "tp_tr_bq")
    pipe = sys.modules["server.pipeline"]

    monkeypatch.setattr(pipe, "is_live",
                        lambda svc: svc == "bigquery")
    import server.services.bigquery_patch as bqp
    monkeypatch.setattr(bqp, "verify_bid",
                        lambda bid: {"id": bid, "experienceName": "Wieliczka"})
    import server.services.bigquery as bq
    monkeypatch.setattr(bq, "_get_booking_extra", lambda *a, **k: {})

    s = live_db.SessionLocal()
    s.add(live_db.RcaDraft(
        id="d_tp_tr_bq", review_id="tp_tr_bq",
        booking={"id": "31246072"}, selected_candidate_bid="31246072",
        match_tier=2, match_confidence="confirmed", candidate_state=False,
        confidence_trail=MATCHING + SUPERSEDED))
    s.commit(); s.close()

    asyncio.run(pipe.process_review("tp_tr_bq"))

    s = live_db.SessionLocal()
    d = s.query(live_db.RcaDraft).filter_by(review_id="tp_tr_bq").first()
    said = " ".join(e.get("text", "") for e in (d.confidence_trail or []))
    s.close()

    assert "Associate confirmed" in said, \
        "the branch under test was not reached — the stub no longer works"
    assert "Author parsed" in said, "the matching history was lost"
    assert "lookup that never ran" not in said, \
        "a superseded Zendesk line survived on the verified-BID path"
    assert "step(s) from the earlier run were removed" in said
