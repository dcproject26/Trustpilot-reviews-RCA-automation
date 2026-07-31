"""One store per value, and a delete that stays deleted.

The v4 columns are projections of rca_v3, so the same value exists twice. That
is survivable only under two rules, and both are easy to break by accident:

  1. The reader falls back on PRESENCE, not truthiness. The dangerous value is
     not a missing one, it is a deliberately emptied one - delete the last flag
     and the dashboard sends `flags: []`. Under a truthiness fallback the empty
     list loses to the populated column and the flag comes back on the next
     load: the delete appears to work, then undoes itself.

  2. Nothing but the pipeline writes the columns. A second writer makes the two
     copies drift regardless of what the reader prefers.

These are the tests that fail if either rule is quietly dropped.
"""
import importlib
import os
import tempfile

import pytest


API_SRC = open("server/api.py", encoding="utf-8").read()
DB_SRC  = open("server/db.py", encoding="utf-8").read()


@pytest.fixture()
def app_env(monkeypatch):
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp.name}")
    import server.config as cfg
    importlib.reload(cfg)
    import server.db as db
    importlib.reload(db)
    db.init_db()
    import server.api as api
    importlib.reload(api)
    yield db, api
    os.unlink(tmp.name)


FLAG = {"team": "CE", "flag": "First reply after SLA", "evidence": "40 minutes."}
LOG  = {"time": "22 Jul 15:41", "what": "Voucher issued", "detail": None}


def _seed(db, rid="tp_w1", **over):
    """A draft as the pipeline leaves it: rca_v3 and the columns agreeing."""
    fields = dict(
        rca_v3={"flags": [FLAG], "booking_logs": [LOG],
                "takedown": {"verdict": "No"},
                "what_went_wrong": {"guest_issues": [{"issue": "Late voucher"}]}},
        flags=[FLAG], booking_logs=[LOG], takedown={"verdict": "No"},
        guest_issues=[{"issue": "Late voucher"}],
    )
    fields.update(over)
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="Sven Test", body_original="no voucher", status="draft"))
        s.add(db.RcaDraft(id=f"draft_{rid}", review_id=rid, **fields))
        s.commit()
    finally:
        s.close()
    return rid


def _patch(db, api, rid, body: dict):
    s = db.SessionLocal()
    try:
        out = api.patch_draft_v2(rid, api.DraftPatchV2(**body), db=s)
        s.commit()
        return out["draft"]
    finally:
        s.close()


def _load(db, api, rid):
    s = db.SessionLocal()
    try:
        d = s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
        return api._draft_dict(d), d
    finally:
        s.close()


# ── rule 1: presence, not truthiness ────────────────────────────────────────

def test_deleting_the_last_flag_does_not_resurrect_it(app_env):
    """The dashboard's flag-delete sets rca_v3.flags = [] and PATCHes the whole
    object. Under a truthiness fallback the column wins and the flag is back on
    the next load."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"rca_v3": {"flags": [], "takedown": {"verdict": "No"}}})
    out, row = _load(db, api, rid)
    assert out["flags"] == [], \
        f"the deleted flag came back from the column: {out['flags']!r}"
    assert row.flags == [FLAG], \
        "the column should be untouched — it is the pipeline's copy, not a second store"


def test_deleting_the_last_booking_log_does_not_resurrect_it(app_env):
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"booking_logs": []})
    assert _load(db, api, rid)[0]["booking_logs"] == []


def test_an_explicit_null_also_beats_a_populated_column(app_env):
    """Clearing a field to null is an answer, not an absence."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"rca_v3": {"flags": None}})
    out = _load(db, api, rid)[0]
    assert out["flags"] == [], f"null lost to the column: {out['flags']!r}"


def test_emptying_a_nested_section_is_honoured(app_env):
    """guest_issues sits under what_went_wrong, so the presence walk has to
    hold for every segment of the path, not just the last."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"guest_issues": []})
    assert _load(db, api, rid)[0]["guest_issues"] == []


def test_a_field_rca_v3_never_mentions_still_falls_back_to_the_column(app_env):
    """The fallback has to keep working, or a draft written before the v4
    deploy renders empty."""
    db, api = app_env
    rid = _seed(db, rca_v3={})
    out = _load(db, api, rid)[0]
    assert out["flags"] == [FLAG]
    assert out["guest_issues"] == [{"issue": "Late voucher"}]
    assert out["takedown"] == {"verdict": "No"}


def test_an_edit_still_wins_over_the_column(app_env):
    db, api = app_env
    rid = _seed(db)
    edited = dict(FLAG, flag="First reply after SLA — 2 breaches")
    _patch(db, api, rid, {"flags": [edited]})
    assert _load(db, api, rid)[0]["flags"] == [edited]


# ── rule 2: one writer ──────────────────────────────────────────────────────

def test_patching_a_section_edits_rca_v3_and_leaves_the_column_alone(app_env):
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"flags": [], "takedown": {"verdict": "Yes"}})
    out, row = _load(db, api, rid)
    assert row.rca_v3["flags"] == [] and row.rca_v3["takedown"] == {"verdict": "Yes"}
    assert row.flags == [FLAG] and row.takedown == {"verdict": "No"}, \
        "the client wrote a column; the two copies can now disagree"
    assert out["takedown"] == {"verdict": "Yes"}


def test_a_section_patch_does_not_discard_the_rest_of_rca_v3(app_env):
    """Writing the whole blob from one section would wipe every other field."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"flags": []})
    row = _load(db, api, rid)[1]
    assert row.rca_v3["booking_logs"] == [LOG]
    assert row.rca_v3["what_went_wrong"]["guest_issues"] == [{"issue": "Late voucher"}]


def test_a_section_patch_counts_as_a_human_edit(app_env):
    """rca_v3_edited_at is what stops a bulk re-run overwriting someone's work."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"flags": []})
    assert _load(db, api, rid)[1].rca_v3_edited_at is not None


def test_the_client_never_writes_a_projection_column():
    """The patch loop assigns straight onto the draft row. A v4 section listed
    there is a second writer, whatever the reader prefers."""
    i = API_SRC.find("def patch_draft_v2(")
    loop = API_SRC[i:API_SRC.find("_sent = patch.model_fields_set", i)]
    for col in ("guest_issues", "sop_compliance", "booking_logs", "flags",
                "takedown", "dss"):
        assert f'"{col}"' not in loop, \
            f"{col} is client-writable as a column again — the copies will drift"


def test_suggested_response_is_not_separately_editable(app_env):
    """final_response is the human's version. Two editable stores for one piece
    of text is the same bug in miniature."""
    _, api = app_env
    fields = api.DraftPatchV2.model_fields
    assert "suggested_response" not in fields
    assert "final_response" in fields


def test_an_unknown_field_in_a_patch_cannot_reach_a_column(app_env):
    """Pydantic drops what it does not declare. Worth pinning: if someone
    turns on extra='allow' to be helpful, every column becomes writable."""
    db, api = app_env
    rid = _seed(db)
    _patch(db, api, rid, {"suggested_response": "smuggled"})
    assert _load(db, api, rid)[1].suggested_response is None


def test_the_schema_says_what_the_columns_are():
    """The next person to add a column copies whatever pattern they find."""
    for col in ("sop_compliance", "booking_logs", "flags", "takedown",
                "dss", "guest_issues"):
        line = next(l for l in DB_SRC.splitlines()
                    if l.strip().startswith(f"{col} ") and "Column(" in l)
        assert "projection of rca_v3" in line, \
            f"{col} does not say it is a projection; it reads as a field of its own"


# ── facts and interpretation are different kinds of thing ───────────────────

def test_the_model_cannot_displace_a_zendesk_derived_frame(app_env):
    """This is not one store with two writers, it is a fact source and an
    interpretation source. Under a shared key, presence-based reading would let
    the model's account replace the rows built from real tickets."""
    db, api = app_env
    frames = [{"ticket_id": "4491", "time": "22 Jul 15:41", "thread": "email",
               "guestSaid": "Where are my tickets?", "weDid": "Resent them."}]
    rid = _seed(db, support_interaction_frames=frames,
                rca_v3={"support_interaction_notes": [
                    {"zd_ref": "ZD-4491", "summary": "Guest chased the voucher.",
                     "ce_miss": "No proactive update after the first failure."}]})
    out = _load(db, api, rid)[0]
    assert out["support_interaction"] == frames, \
        "the model's account replaced the rows built from real tickets"
    assert out["support_interaction_notes"][0]["ce_miss"], \
        "the interpretation must still reach the renderer"


def test_the_two_keys_never_collide(app_env):
    """Distinct keys are what makes the precedence structural rather than a
    rule someone has to remember."""
    db, api = app_env
    rid = _seed(db, support_interaction_frames=[{"ticket_id": "1"}], rca_v3={})
    out = _load(db, api, rid)[0]
    assert out["support_interaction"] == [{"ticket_id": "1"}]
    assert out["support_interaction_notes"] == []


def test_sp_facts_and_sp_interpretation_are_also_split(app_env):
    db, api = app_env
    rid = _seed(db, sp_interaction_frames=[{"ticket_id": "7", "time": "22 Jul 16:02"}],
                rca_v3={"sp_interaction_notes": {"raised": "Yes", "records": [
                    {"zd_ref": "ZD-7", "summary": "Operator confirmed the no-show."}]}})
    out = _load(db, api, rid)[0]
    assert out["sp_interaction"] == [{"ticket_id": "7", "time": "22 Jul 16:02"}]
    assert out["sp_interaction_notes"]["raised"] == "Yes"


def test_area_of_improving_follows_the_same_presence_rule(app_env):
    """It was the one v4-shaped field still read column-first, so a draft whose
    points live only in rca_v3 rendered an empty section — and an operator who
    deleted the last point would have had it resurrected by the column."""
    db, api = app_env
    rid = _seed(db, area_of_improving=["stale from the column"],
                rca_v3={"area_of_improving": ["Surface the delivery window."]})
    assert _load(db, api, rid)[0]["area_of_improving"] == ["Surface the delivery window."]


def test_deleting_the_last_improvement_point_does_not_resurrect_it(app_env):
    db, api = app_env
    rid = _seed(db, area_of_improving=["stale from the column"],
                rca_v3={"area_of_improving": []})
    assert _load(db, api, rid)[0]["area_of_improving"] == []


def test_a_draft_with_no_v3_points_still_falls_back(app_env):
    db, api = app_env
    rid = _seed(db, area_of_improving=["written by an older pipeline"], rca_v3={})
    assert _load(db, api, rid)[0]["area_of_improving"] == ["written by an older pipeline"]
