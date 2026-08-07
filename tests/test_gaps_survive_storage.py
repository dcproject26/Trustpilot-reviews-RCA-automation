"""Actions Taken's source must survive the round trip through storage.

THE DEFECT, FOUND ON A REAL CARD. `validate` read `wwr["gaps"]`, built the
tabs from it, and returned a `what_went_wrong` carrying guest_issues, fixes
and case_findings — and nothing else. The gaps were consumed and dropped.

Everything downstream then read a card with no gaps:

  * `trace_actions.py` reported `gaps` ABSENT on a draft generated minutes
    earlier by a prompt that demands them
  * `PATCH /draft-v2` rebuilds the column from the STORED gaps, so every card
    edit regrouped Actions Taken to empty
  * both re-run paths passed the WHOLE previous column as `keep`, so the rows
    the old fixes-derived section had produced were carried forward forever —
    four recommendation-shaped rows on a CO tab that no current gap explained,
    with nothing saying they were stale

test_actions_stay_in_step.py did not catch it because it PATCHes a blob
containing gaps in the same request: the value never makes a round trip
through the database, which is the only place it was being lost.
"""
import pytest
from fastapi.testclient import TestClient

from server.services.rca_v4_validate import validate
from server.checklist import hand_typed_actions, actions_from_gaps


GAP = {"gap": "Chat miss — the guest asked to revert to 08:30 and nobody "
              "followed up", "team": "CO", "source_ref": "ZD-34335318"}


def _wwr(**kw):
    return {"what_went_wrong": kw}


# ── the key is stored, not just consumed ───────────────────────────────────

def test_gaps_come_back_out_of_validate():
    out, _ = validate(_wwr(guest_issues=[], fixes=[], gaps=[GAP]))
    assert out["what_went_wrong"]["gaps"] == [GAP], \
        out["what_went_wrong"].get("gaps")


def test_the_stored_gap_still_builds_the_tab_on_a_second_pass():
    """THE ROUND TRIP. Feed validate its own output — which is exactly what
    PATCH /draft-v2 and a regenerate do — and the tab must survive it."""
    once, _ = validate(_wwr(guest_issues=[], fixes=[], gaps=[GAP]))
    twice, _ = validate(once)
    assert twice["what_went_wrong"]["gaps"] == [GAP]
    tabs, _ = actions_from_gaps(twice["what_went_wrong"]["gaps"])
    assert tabs["co"] == [GAP["gap"]], tabs


def test_an_empty_gap_list_is_stored_as_a_list_not_dropped():
    """[] and "absent" are different answers: one is a case with nothing
    outstanding, the other is a draft that predates the field."""
    out, _ = validate(_wwr(guest_issues=[], fixes=[], gaps=[]))
    assert out["what_went_wrong"]["gaps"] == []


def test_a_gap_with_no_text_takes_no_row():
    out, _ = validate(_wwr(guest_issues=[], fixes=[],
                           gaps=[{"gap": "  ", "team": "CO"}, GAP]))
    assert out["what_went_wrong"]["gaps"] == [GAP]


def test_an_unsourced_gap_is_stored_so_the_gate_can_still_count_it():
    """Filtering it here too would remove the count that makes the
    anti-hallucination gate visible — the section would just be smaller."""
    bare = {"gap": "Require proactive outreach", "team": "CO",
            "source_ref": ""}
    out, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[bare]))
    kept = out["what_went_wrong"]["gaps"]
    assert len(kept) == 1 and kept[0]["gap"] == bare["gap"], kept
    # `_clean` normalises an empty string to None, which every reader of this
    # field already treats as absent. What matters is that the ROW survived to
    # be counted, not which flavour of empty the ref carries.
    assert not kept[0]["source_ref"], kept
    assert any("cited no ticket" in n for n in notes), notes


def test_gaps_of_the_wrong_shape_say_so_rather_than_reading_as_empty():
    out, notes = validate(_wwr(guest_issues=[], fixes=[], gaps={"a": 1}))
    assert out["what_went_wrong"]["gaps"] == []
    assert any("NOT read" in n for n in notes), notes


# ── keep carries a PERSON's rows, not the model's ──────────────────────────

def test_a_row_the_previous_gaps_explain_is_not_kept():
    """THE FOUR STALE ROWS. Model output that the rebuild would produce again
    must not also arrive as `keep`, or it becomes immune to the rebuild."""
    prev_tabs, _ = actions_from_gaps([GAP])
    keep, unattributed = hand_typed_actions(prev_tabs, [GAP])
    assert keep == {}, keep
    assert unattributed == 0


def test_a_row_no_gap_explains_is_kept_as_a_persons():
    prev_tabs, _ = actions_from_gaps([GAP])
    prev_tabs["co"].append("Ring the guest on Monday")
    keep, unattributed = hand_typed_actions(prev_tabs, [GAP])
    assert keep == {"co": ["Ring the guest on Monday"]}, keep
    assert unattributed == 0, "the previous gaps were stored — this is known"


def test_rows_on_a_draft_with_no_stored_gaps_are_carried_but_counted():
    """The state every existing card is in. Nothing can be subtracted, so
    nothing can be attributed — and calling those rows hand-typed is the claim
    that was wrong. Carried, because deleting somebody's work on a guess is
    the expensive direction, and COUNTED."""
    keep, unattributed = hand_typed_actions(
        {"co": ["Require RO to verify the slot time", "Ring the guest"]}, None)
    assert keep == {"co": ["Require RO to verify the slot time",
                           "Ring the guest"]}
    assert unattributed == 2


def test_the_unattributed_count_reaches_the_trail_in_words():
    _, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[]),
                        keep_actions={"co": ["a row from before gaps existed"]},
                        keep_unattributed=1)
    said = " ".join(notes)
    assert "could not be traced to a gap OR to a person" in said, notes
    assert "UNVERIFIED" in said


def test_an_attributed_keep_is_not_called_unverified():
    """The inverse bug: warning on every healthy card is how a warning stops
    being read."""
    _, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[]),
                        keep_actions={"co": ["Ring the guest on Monday"]})
    assert not any("UNVERIFIED" in n for n in notes), notes


def test_an_empty_column_needs_no_note_either_way():
    keep, unattributed = hand_typed_actions({}, None)
    assert keep == {} and unattributed == 0


# ── driven through the endpoint, because that is where it was lost ─────────

@pytest.fixture()
def client(live_db):
    from server.main import app
    from server.db import get_session
    app.dependency_overrides[get_session] = lambda: live_db.SessionLocal()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_a_patch_rebuilds_the_tab_from_gaps_it_stored_earlier(live_db, client):
    """THE FAILURE IN ONE TEST. Save gaps, then PATCH something unrelated. The
    handler regroups from the STORED gaps — which, before this, were never
    there, so every edit emptied the tab."""
    s = live_db.SessionLocal()
    s.add(live_db.Review(id="tp_g", rating=1, author="A", body_original="b",
                         status="draft"))
    stored, _ = validate(_wwr(guest_issues=[], fixes=[], gaps=[GAP]))
    s.add(live_db.RcaDraft(id="d_g", review_id="tp_g", rca_v3=stored,
                           actions_taken={"co": [GAP["gap"]]}))
    s.commit(); s.close()

    r = client.patch("/api/reviews/tp_g/draft-v2", json={"resolution": "x"})
    assert r.status_code == 200, r.text
    got = r.json()["draft"]["actions_taken"]
    assert got["co"] == [GAP["gap"]], \
        f"the regroup rebuilt from gaps that were not stored: {got}"


# ── source_ref is a reference, not a description of where to look ──────────
#
# SEEN ON A REAL CARD. The SP gap cited "booking record — escalationEmail
# field is empty", which passes the not-empty gate and is the gap restated: a
# reader cannot open it. The prompt names three shapes — a ZD ticket, a bare
# booking id, or a case finding QUOTED — and the quoted case is checked
# against the actual findings rather than guessed at.
#
# COUNTED, NOT DROPPED. That gap was real; it named a blank field on this
# booking. Losing a true finding to a formatting rule is the expensive
# direction. The count is how a prompt that stops producing openable refs
# becomes visible.

FINDING = {"text": "Escalation email field for this booking is blank; no SP "
                   "escalation channel available", "source": "booking"}


def _gap(ref):
    return {"gap": "Krakville's escalation email is still unpopulated",
            "team": "SP", "source_ref": ref}


def _prose_note(notes):
    return " ".join(n for n in notes if "DESCRIPTION rather than a" in n)


def test_a_ticket_reference_is_accepted_quietly():
    _, notes = validate(_wwr(guest_issues=[], fixes=[],
                             gaps=[_gap("ZD-34335318")]))
    assert not _prose_note(notes), notes


def test_a_bare_booking_id_is_a_reference():
    _, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[_gap("32885089")]))
    assert not _prose_note(notes), notes


def test_a_finding_quoted_from_this_card_is_a_reference():
    _, notes = validate(_wwr(guest_issues=[], fixes=[],
                             case_findings=[FINDING],
                             gaps=[_gap("Escalation email field for this "
                                        "booking is blank")]))
    assert not _prose_note(notes), notes


def test_a_description_of_where_to_look_is_counted():
    """THE ROW FROM THE CARD, verbatim."""
    out, notes = validate(_wwr(guest_issues=[], fixes=[],
                               case_findings=[FINDING],
                               gaps=[_gap("booking record — escalationEmail "
                                          "field is empty")]))
    assert "1 gap(s) cite a DESCRIPTION" in _prose_note(notes), notes
    assert len(out["what_went_wrong"]["gaps"]) == 1, \
        "a real gap was dropped for a formatting rule"


def test_a_vague_pointer_is_counted_too():
    _, notes = validate(_wwr(guest_issues=[], fixes=[],
                             case_findings=[FINDING],
                             gaps=[_gap("the chat transcript")]))
    assert _prose_note(notes), notes


def test_quoting_a_finding_this_card_does_not_have_is_not_a_reference():
    """The check is against the findings that are HERE. Prose that matches
    nothing is prose."""
    _, notes = validate(_wwr(guest_issues=[], fixes=[], case_findings=[],
                             gaps=[_gap("Escalation email field for this "
                                        "booking is blank")]))
    assert _prose_note(notes), notes


def test_an_unsourced_gap_is_not_also_counted_as_a_description():
    """It is already dropped and counted by the gate. Two notes for one row
    reads as two problems."""
    _, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[_gap("")]))
    assert not _prose_note(notes), notes
    assert any("cited no ticket" in n for n in notes), notes


# ── both grains reach the model ────────────────────────────────────────────
#
# ON A REAL CARD ONLY THE PROCESS GRAIN CAME BACK. The gaps were "Krakville's
# escalation email is unpopulated" and "No process requires RO to confirm the
# new operator's pickup time" — both true, both process-level, and THIS
# guest's unfinished business missing entirely. The chat miss is the prompt's
# own worked example and it did not appear.
#
# A team reading only the process gap does not know a specific guest is still
# waiting. These drive `rca_v3_prompt()`, the string the model receives —
# asserting against RCA_V4_TEMPLATE would pass against broken substitution.

def _assembled():
    from server import prompts
    return " ".join(prompts.rca_v3_prompt(
        review_text="x", booking={}, timeline=[], insights={}, dss_rec={},
        l1="", l2="", sub_theme="", support_summary="", checklist={},
        review_id="r1").split())


def test_the_prompt_asks_for_the_case_grain_and_names_it_first():
    out = _assembled()
    assert "GAPS COME AT TWO GRAINS AND YOU OWE BOTH" in out
    assert "START WITH THE CASE GRAIN" in out


def test_it_says_why_the_case_grain_is_the_one_that_gets_skipped():
    """A rule with no reason is the first thing lost in an edit."""
    assert "does not know a specific guest is still waiting" in _assembled()


def test_being_answered_is_not_being_solved():
    """The likely reason it was skipped: an agent DID reply, so the case reads
    as handled. The guest was left exactly as stuck as before."""
    out = _assembled()
    assert "A GUEST PROBLEM UNSOLVED AT CASE CLOSE IS ALWAYS A GAP" in out
    assert "Being answered is not being solved" in out


def test_goodwill_paid_afterwards_does_not_close_it():
    """This case issued a wallet credit the day after. That is compensation,
    not the 08:30 slot the guest asked for."""
    assert "compensation, not the thing the guest asked for" in _assembled()


def test_the_prompt_shows_the_reference_shapes_with_the_row_that_failed():
    out = _assembled()
    assert "A REFERENCE, NOT A DESCRIPTION" in out
    assert "booking record — escalationEmail field is empty" in out, \
        "the NO example is the row from the card; a rule without its own " \
        "counter-example is the one that gets re-broken"


def test_prose_that_merely_contains_a_booking_id_is_still_prose():
    """SURVIVED A MUTATION. The id test was `fullmatch`; loosening it to a
    substring search passed the whole suite, because every prose example here
    happened to carry no digits.

    "the rebooking intimation sent on booking 32885089" names where to look
    and is not something a reader can open. A bare id IS the record; a
    sentence with an id in it is a description with a number in it."""
    _, notes = validate(_wwr(guest_issues=[], fixes=[], case_findings=[FINDING],
                             gaps=[_gap("the rebooking intimation sent on "
                                        "booking 32885089")]))
    assert _prose_note(notes), notes


def test_an_id_with_a_hash_or_stray_space_is_still_the_record():
    """The inverse. Tightening far enough to reject "#32885089 " would file a
    real reference as prose, which is the noise that stops a count being
    read."""
    for ref in ("32885089", " 32885089 ", "#32885089", "32885089."):
        _, notes = validate(_wwr(guest_issues=[], fixes=[], gaps=[_gap(ref)]))
        assert not _prose_note(notes), (ref, notes)
