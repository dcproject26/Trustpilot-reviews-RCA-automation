"""The DSS lookup can match the wrong row, so it has to be correctable.

The card showed a matched DSS row with its text and a routing line, and the
lookup had matched a delay/late-guide row against a case about a vendor
reassignment. There was no way for an associate to fix it: `state.dssEdit`
existed and the ✎ Edit button toggled it, but the editable span was only
rendered when `prescribes` was already non-empty, and nothing marked a
corrected row as corrected.

Three requirements, and this file drives all three:

  1. IT PERSISTS. There is prior art in this repo for a control that looked
     live and saved nothing (TL;DR, and `area_of_improving` written to a column
     nobody read), so the edit goes through the generic data-v3p saver into
     rca_v3 and comes back on the next load.
  2. IT IS DISTINGUISHABLE. A row a person corrected and a row the scorer
     picked must not render identically — the same reason area_of_improving
     marks a hand-added point "by hand" against a derived one.
  3. DOWNSTREAM READS THE EDIT. The prompt used to be handed `d.dss_rec`, the
     LOOKUP's value, so a re-run discarded the correction it was asked to act
     on. Two stores for one fact, and the prompt was reading the one the
     person could not change.

The browser half is in tests/test_recent_changes_rendered.py; this file drives
the server contract, which is where the two-stores half lives.
"""
import pytest

from server.api import _dss_for_prompt
from server.db import RcaDraft


def _d(**kw):
    return RcaDraft(id="d1", review_id="tp_1", **kw)


LOOKUP = {"dss": "If delay is under 20 minutes, offer 25% Headout credits.",
          "compensation": "25% credits", "ref": "https://example/dss/row/1"}
CORRECTION = "Reassignment without consent: refund in full and re-book."


def test_an_uncorrected_draft_still_gets_the_lookup():
    """The control. If the lookup stopped reaching the prompt, every
    assertion below would be satisfiable by a function that returns nothing."""
    out = _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3={}))
    assert out == LOOKUP


def test_a_hand_corrected_row_replaces_what_the_prompt_is_told():
    out = _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3={
        "dss": {"prescribes": CORRECTION, "by_hand": True}}))
    assert out["dss"] == CORRECTION, (
        "the prompt is still being told the row the lookup matched, so a "
        "re-run discards the correction it was asked to act on")
    assert out["prescribes"] == CORRECTION
    assert out["corrected_by_hand"] is True, (
        "nothing downstream can tell a corrected row from a matched one")


def test_the_lookups_other_fields_survive_the_correction():
    """Only the prescription was corrected. Dropping the rest would throw away
    the compensation and the row reference nobody asked to change."""
    out = _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3={
        "dss": {"prescribes": CORRECTION, "by_hand": True}}))
    assert out["compensation"] == "25% credits"
    assert out["ref"] == LOOKUP["ref"]


def test_an_untouched_projection_does_not_shadow_the_lookup():
    """rca_v3.dss also holds the pipeline's own projection of the lookup. Only
    a value someone TYPED may win, or the richer lookup record is replaced by
    its own summary on every card."""
    out = _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3={
        "dss": {"prescribes": "a projection nobody edited"}}))
    assert out == LOOKUP, (
        "an unedited projection displaced the lookup — by_hand is what makes "
        "this an edit rather than a copy")


def test_a_marked_row_with_no_text_does_not_blank_the_prompt():
    """Marked but empty is not a correction. Letting it through would tell the
    model the playbook prescribes nothing, which is a claim nobody made."""
    out = _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3={
        "dss": {"prescribes": "", "by_hand": True}}))
    assert out == LOOKUP


def test_a_draft_with_no_lookup_at_all_still_carries_a_correction():
    """The case the edit exists for: nothing matched, and a person wrote the
    prescription themselves."""
    out = _dss_for_prompt(_d(dss_rec=None, rca_v3={
        "dss": {"prescribes": CORRECTION, "by_hand": True}}))
    assert out["prescribes"] == CORRECTION
    assert out["corrected_by_hand"] is True


def test_malformed_stores_do_not_raise():
    """It sits in the regenerate path; raising here would turn a re-run into a
    500 for a draft with an odd shape."""
    for v3 in (None, [], {"dss": "a string"}, {"dss": None}, {}):
        assert _dss_for_prompt(_d(dss_rec=dict(LOOKUP), rca_v3=v3)) == LOOKUP


def test_the_edit_survives_a_round_trip_through_the_api():
    """It PERSISTS. rca_v3 is the store the card writes and the card reads, so
    a correction has to come back on the next load rather than only living in
    the DOM until the next render."""
    import importlib, tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp.name}"
    os.environ["MOCK_MODE"] = "true"
    try:
        import server.config as cfg; importlib.reload(cfg)
        import server.db as db; importlib.reload(db); db.init_db()
        import server.api as api; importlib.reload(api)
        import server.main as main; importlib.reload(main)
        from fastapi.testclient import TestClient
        s = db.SessionLocal()
        try:
            s.add(db.Review(id="tp_d", slack_ts="1", slack_channel="C1",
                            rating=1, author="D", body_original="x",
                            status="draft"))
            s.add(db.RcaDraft(id="d_d", review_id="tp_d",
                              dss_rec=dict(LOOKUP),
                              rca_v3={"dss": {"prescribes": LOOKUP["dss"]}}))
            s.commit()
        finally:
            s.close()
        c = TestClient(main.app)
        before = c.get("/api/reviews/tp_d").json()["draft"]
        assert before["dss"]["prescribes"] == LOOKUP["dss"], before["dss"]

        r = c.patch("/api/reviews/tp_d/draft-v2", json={"rca_v3": {
            "dss": {"prescribes": CORRECTION, "by_hand": True}}})
        assert r.status_code == 200, r.text
        after = c.get("/api/reviews/tp_d").json()["draft"]
        assert after["dss"]["prescribes"] == CORRECTION, (
            f"the correction did not survive the round trip: {after['dss']}")
        assert after["dss"]["by_hand"] is True, (
            "the marker did not persist, so a corrected row renders as a "
            "matched one on the next load")
    finally:
        os.unlink(tmp.name)
