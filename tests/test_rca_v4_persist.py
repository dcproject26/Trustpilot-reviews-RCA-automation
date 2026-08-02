"""The v4 output has to survive the trip to the database and back.

Two things go wrong silently here. A validation layer that nothing calls looks
exactly like one that works — every test passes, and raw model tokens still
reach the screen. And a second write path that drifts from the first leaves
half the columns holding the previous run's answer, which reads as data rather
than as staleness.

`regenerate-rca` is the one persist path that can be driven end to end without
the whole pipeline, so it is the one exercised for real. The pipeline's own
save is asserted at source, since it lives inside a 700-line coroutine that
cannot be entered without Zendesk, BigQuery and Anthropic.
"""
import asyncio
import importlib
import os
import tempfile

import pytest


PIPE = open("server/pipeline.py", encoding="utf-8").read()
API  = open("server/api.py", encoding="utf-8").read()

# What the model returns: every enum wrong, in the ways the handoff observed.
DIRTY_RCA = {
    "stated_issue": "The voucher never arrived.",
    "tldr": {"our_mistake": "We did not send it.", "our_fix": "Refunded."},
    "l1": "Refund Issue", "l2": "Delayed Refund",          # not in the taxonomy
    "sub_themes": ["C. Ticket Delayed"],
    "scenarios": ["Ticket delivery delay"],
    "what_went_wrong": {"guest_issues": [{
        "issue": "Voucher never delivered",
        "claim": "I waited two hours and nothing came.",
        "claim_accuracy": "probably fine tbh",              # → Unknown
        "owner": "RO",
        "root_cause": "The fulfilment run failed silently.",
        "evidence": ["[booking] Two adult tickets, unissued."],   # legacy string
    }]},
    "issue_specific_answers": [
        {"question": "How long until first reply?",
         "verdict": "28 minutes (first agent)"},            # → evidence
    ],
    "sop_compliance": {"verdict": "mostly followed"},       # → unknown
    "booking_logs": [{"time": "Unknown", "what": "Fulfilment attempted"}],
    "flags": [{"team": "Growth", "flag": "No alert on failed fulfilment",
               "evidence": "Three retries, no page."}],     # team → null
    "takedown": {"verdict": "maybe"},                       # → Untraceable
    "dss": {"prescribes": "Refund in full.", "ref": None},
    "resolution": "Full refund of EUR 84.",
    "suggested_response": "I'm sorry your tickets never arrived. "
                          "We have refunded you in full.",
}


@pytest.fixture()
def app_env(monkeypatch):
    """A throwaway SQLite DB with the real schema, and an api bound to it."""
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


def _seed(db, rid="tp_v4_1"):
    s = db.SessionLocal()
    try:
        s.add(db.Review(id=rid, slack_ts="1.0", slack_channel="C1", rating=1,
                        author="David Test", body_original="no voucher ever came",
                        status="draft"))
        s.add(db.RcaDraft(id=f"draft_{rid}", review_id=rid,
                          l1="Operations Issue", l2="Ticket Issues",
                          # what a previous run left behind
                          flags=[{"team": "CE", "flag": "stale"}],
                          resolution="Nothing offered yet",
                          suggested_response="an older reply"))
        s.commit()
    finally:
        s.close()
    return rid


def _regenerate(db, api, rid, rca=DIRTY_RCA, seen=None):
    async def _fake_rca(**kw):
        if seen is not None:
            seen.update(kw)
        return dict(rca)

    from server.services import claude as claude_svc
    from server.services import rca_checklist
    claude_svc.generate_rca_v3 = _fake_rca

    async def _no_checklist(l1, l2):
        return []
    rca_checklist.get_checklist = _no_checklist

    s = db.SessionLocal()
    try:
        out = asyncio.run(api.regenerate_rca(
            rid, api.ScenarioRegen(scenarios=[]), db=s))
        s.commit()
    finally:
        s.close()
    return out


def _reload(db, rid):
    s = db.SessionLocal()
    try:
        return s.query(db.RcaDraft).filter(db.RcaDraft.review_id == rid).first()
    finally:
        s.close()


# ── the validator has to actually run on this path ──────────────────────────

def test_regenerating_runs_the_validator_rather_than_storing_raw_output(app_env):
    """Every coercion below is one the validator makes. If the endpoint stores
    the model's dict untouched they all fail together, which is the point:
    a layer nothing calls is indistinguishable from one that works."""
    db, api = app_env
    rid = _seed(db)
    out = _regenerate(db, api, rid)
    v3 = out["rca_v3"]

    assert v3["l1"] == "Miscellaneous Issue", "an invented category was stored as given"
    assert v3["l1_raw"] == "Refund Issue", "the model's raw value must stay recoverable"
    assert v3["what_went_wrong"]["guest_issues"][0]["claim_accuracy"] == "Unknown"
    assert v3["what_went_wrong"]["guest_issues"][0]["evidence"][0]["source"] == "booking"
    assert v3["issue_specific_answers"][0]["verdict"] == "Unknown"
    assert "28 minutes" in v3["issue_specific_answers"][0]["evidence"]
    assert v3["sop_compliance"]["verdict"] == "unknown"
    assert v3["takedown"]["verdict"] == "Untraceable"
    assert v3["booking_logs"][0]["time"] is None
    assert v3["flags"][0]["team"] == "OTHER"
    assert v3["flags"][0]["team_raw"] == "Growth"


def test_the_coercions_are_reported_not_applied_silently(app_env):
    db, api = app_env
    rid = _seed(db)
    out = _regenerate(db, api, rid)
    assert any("taxonomy" in n for n in out["validation_notes"])


# ── every v4 column is written, not just rca_v3 ─────────────────────────────

def test_the_v4_columns_are_written_on_regeneration(app_env):
    """Writing rca_v3 alone left these holding the previous run's answer —
    which reads as data, not as staleness."""
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    d = _reload(db, rid)

    assert d.guest_issues and d.guest_issues[0]["issue"] == "Voucher never delivered"
    assert d.sop_compliance["verdict"] == "unknown"
    assert d.booking_logs and d.booking_logs[0]["what"] == "Fulfilment attempted"
    assert d.takedown["verdict"] == "Untraceable"
    assert d.dss["prescribes"] == "Refund in full."
    assert d.flags[0]["flag"] == "No alert on failed fulfilment", \
        "the stale flag from the previous run is still there"


def test_the_reply_and_resolution_come_from_the_rca_now(app_env):
    """v4 emits both. Leaving them meant a fresh RCA sat next to a reply
    written against the previous one."""
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    d = _reload(db, rid)
    assert d.resolution == "Full refund of EUR 84."
    assert "refunded you in full" in d.suggested_response


def test_issue_specific_answers_are_stored_as_a_list(app_env):
    """v3 stored {question: answer}. A dict here renders nothing in v4.

    Both cases matter, and only the empty one exercises the default: an RCA
    with answers is a list either way, so a test that only covers that passes
    against the v3 fallback.
    """
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    assert isinstance(_reload(db, rid).issue_specific_answers, list)

    no_answers = dict(DIRTY_RCA, issue_specific_answers=[])
    _regenerate(db, api, rid, rca=no_answers)
    got = _reload(db, rid).issue_specific_answers
    assert got == [] and isinstance(got, list), \
        f"an RCA with no answers stored {got!r}, which the v4 renderer cannot read"


def test_regenerating_passes_the_approved_reply_voice(app_env, monkeypatch):
    """The reply now comes from the RCA call, so the tone reference has to
    reach it — on this path too, or a regenerated reply drifts out of the
    approved register while the pipeline's stays in it."""
    db, api = app_env
    canned = [{"situation": "Ticket delivered late", "response": "So sorry."}]

    async def _canned(l1, l2, sub_theme, text):
        return canned
    import server.services.canned as canned_mod
    monkeypatch.setattr(canned_mod, "get_canned_responses", _canned)

    rid, seen = _seed(db), {}
    _regenerate(db, api, rid, seen=seen)
    assert seen.get("canned_list") == canned, \
        "regenerate-rca calls the model with no voice reference"


def test_a_dead_canned_sheet_does_not_take_the_rca_down(app_env, monkeypatch):
    """The sheet 403s in this environment already. A tone reference is a nice
    to have; the RCA is not."""
    db, api = app_env

    async def _boom(*a, **k):
        raise RuntimeError("403 Forbidden")
    import server.services.canned as canned_mod
    monkeypatch.setattr(canned_mod, "get_canned_responses", _boom)

    rid, seen = _seed(db), {}
    out = _regenerate(db, api, rid, seen=seen)
    assert out["ok"] and seen.get("canned_list") == []


# ── the pipeline's own save, asserted at source ─────────────────────────────


def test_the_pipeline_passes_the_approved_reply_voice():
    i = PIPE.find("claude.generate_rca_v3(")
    assert "canned_list=canned_list" in PIPE[i:i + 1200], \
        "the pipeline generates a reply with no voice reference"
    assert "get_canned_responses(" in PIPE[max(0, i - 1200):i], \
        "canned_list is passed but never looked up"



def test_the_pipeline_validates_before_it_persists():
    assert "rca_v4_validate import validate" in PIPE, \
        "the pipeline stores the model's RCA without validating it"
    i, j = PIPE.find("_validate_rca(rca_v3"), PIPE.find("draft.rca_v3 ")
    assert 0 < i < j, "validation must run before the draft is written"


def test_the_projection_maps_every_column_to_its_place_in_the_rca():
    """Driven, not asserted at source. `assert "draft.flags " in PIPE` passes
    just as happily against a build where the line it names is unreachable —
    which is how two guarantees in this file turned out to guard nothing."""
    from server.services.rca_v4_validate import project_v4
    out = project_v4({
        "what_went_wrong": {"guest_issues": [{"issue": "x"}]},
        "sop_compliance": {"verdict": "deviated"},
        "booking_logs": [{"what": "issued"}],
        "flags": [{"team": "CE", "flag": "late"}],
        "takedown": {"verdict": "No"},
        "dss": {"prescribes": "refund"},
        "issue_specific_answers": [{"question": "q", "verdict": "Yes"}],
    })
    assert out["guest_issues"] == [{"issue": "x"}]
    assert out["sop_compliance"] == {"verdict": "deviated"}
    assert out["booking_logs"] == [{"what": "issued"}]
    assert out["flags"] == [{"team": "CE", "flag": "late"}]
    assert out["takedown"] == {"verdict": "No"}
    assert out["dss"] == {"prescribes": "refund"}
    assert out["issue_specific_answers"] == [{"question": "q", "verdict": "Yes"}]


def test_an_absent_section_projects_to_its_empty_type():
    """A dict where the renderer wants a list renders nothing, silently."""
    out = __import__("server.services.rca_v4_validate", fromlist=["x"]).project_v4({})
    assert out["guest_issues"] == [] and out["booking_logs"] == []
    assert out["flags"] == [] and out["issue_specific_answers"] == []
    assert out["sop_compliance"] == {} and out["takedown"] == {} and out["dss"] == {}


def test_the_projection_never_raises_on_a_malformed_rca():
    from server.services.rca_v4_validate import project_v4
    for bad in (None, "nope", 42, [], {"what_went_wrong": "nope"},
                {"what_went_wrong": {"guest_issues": None}}):
        assert isinstance(project_v4(bad), dict)


def test_both_write_paths_use_the_one_projection():
    """Written out twice, the two paths drift — and the drift is invisible,
    because both look like working code. regenerate-rca had already fallen
    behind the pipeline once."""
    for src, name in ((PIPE, "pipeline"), (API, "api")):
        assert "project_v4(" in src, f"{name} still projects the columns by hand"
    assert "draft.flags " not in PIPE and "d.flags  " not in API


def test_the_columns_the_projection_writes_are_the_columns_that_exist():
    """A key with no column silently does nothing on setattr; a column the
    projection forgot goes stale on every run."""
    from server.services.rca_v4_validate import V4_PROJECTION
    from server.db import RcaDraft
    for col in V4_PROJECTION:
        assert hasattr(RcaDraft, col), f"{col} is projected but is not a column"


def test_the_pipeline_does_not_also_run_the_standalone_drafter():
    """Two replies were generated per review and the better-grounded one was
    thrown away, because _draft_dict reads the column the drafter wrote."""
    assert "claude.draft_response_v2(" not in PIPE
    assert 'rca_v3 or {}).get("suggested_response")' in PIPE


def test_a_validation_note_reaches_the_confidence_trail():
    """A coercion the reader cannot see is a silent edit."""
    i = PIPE.find("_validate_rca(rca_v3")
    assert "confidence_trail.append" in PIPE[i:i + 900]


def test_a_coercion_is_marked_warn_not_pass():
    """These sit in the same list as the pipeline's own step results. Marked
    pass, "we changed the model's answer" reads as "a step succeeded" — which
    is how a coerced enum becomes a trusted fact."""
    i = PIPE.find("_validate_rca(rca_v3")
    block = PIPE[i:i + 900]
    j = block.find("confidence_trail.append")
    assert '"mark": "warn"' in block[j:j + 200], \
        "a validator coercion is being reported as a successful step"


def test_every_path_that_produces_an_rca_validates_it():
    """A validator wired into one path looks exactly like one that works —
    which is the defect this whole layer was written to fix."""
    for src, name in ((PIPE, "server/pipeline.py"), (API, "server/api.py")):
        for i, _ in _find_all(src, "generate_rca_v3("):
            after = src[i:i + 3000]
            assert "_validate_rca(" in after, \
                f"{name}: a generate_rca_v3 call at offset {i} never validates its output"


def _find_all(hay: str, needle: str):
    i = hay.find(needle)
    while i >= 0:
        yield i, needle
        i = hay.find(needle, i + 1)


# ── provenance: which prompt wrote this ─────────────────────────────────────

def test_a_regenerated_draft_is_stamped_with_the_prompt_version(app_env):
    """A v3 row read as a v4 checkpoint reports every v3 artefact as a
    validator failure, and nothing on the row says otherwise. That cost a
    re-run once; the stamp is what stops it happening twice."""
    from server.prompts import RCA_PROMPT_VERSION
    db, api = app_env
    rid = _seed(db)
    _regenerate(db, api, rid)
    assert _reload(db, rid).rca_prompt_version == RCA_PROMPT_VERSION


def test_the_pipeline_stamps_only_when_it_produced_an_rca():
    """A failed generation keeps the previous blob, so it must keep that
    blob's version too — claiming v4 over v3 content is worse than no stamp."""
    i = PIPE.find("draft.rca_prompt_version")
    assert i > 0, "the pipeline never stamps the prompt version"
    assert "if _v3:" in PIPE[max(0, i - 300):i], \
        "the stamp is written unconditionally, so a failed RCA is labelled v4"


# ── a join that matches nothing must not look like a model that said nothing ─
#
# Driven for real rather than asserted at source. The first version of these
# checked that the message existed in pipeline.py, which passed against a build
# where the branch producing it was unreachable — the same defect this whole
# layer exists to catch, committed into its own test.

from server.services.rca_v4_validate import contact_join_notes

FRAMES = [{"ticket_id": "4491", "time": "22 Jul 15:41"}]


def test_a_failed_join_is_counted_and_named():
    """The whole bug class: zd_ref "ZD-9999" against ticket_id "4491" matches
    nothing, and a silent zero is indistinguishable from no notes at all."""
    out = contact_join_notes(FRAMES, [], {"support_interaction_notes": [
        {"zd_ref": "ZD-9999", "summary": "a contact on no known ticket"}]})
    assert len(out) == 1
    assert "1 model note(s) could not be joined" in out[0]
    assert "ZD-9999" in out[0], "the reader cannot chase a count with no reference"
    assert "not dropped" in out[0], "without this it reads as data loss"


def test_an_off_zendesk_contact_is_reported_separately():
    """A note with no zd_ref is the model doing what rule 11 asks. Counting it
    with the failed joins would make a working run look faulty."""
    out = contact_join_notes(FRAMES, [], {"support_interaction_notes": [
        {"zd_ref": None, "channel": "call", "summary": "guest says they phoned"}]})
    assert len(out) == 1
    assert "no Zendesk ticket" in out[0]
    assert "could not be joined" not in out[0]


def test_both_kinds_are_reported_as_two_lines():
    out = contact_join_notes(FRAMES, [], {"support_interaction_notes": [
        {"zd_ref": "ZD-9999", "summary": "bad ref"},
        {"zd_ref": None, "summary": "off zendesk"}]})
    assert len(out) == 2


def test_a_clean_join_says_nothing():
    """The only case that should be silent."""
    assert contact_join_notes(FRAMES, [], {"support_interaction_notes": [
        {"zd_ref": "ZD-4491", "summary": "guest chased it"}]}) == []


def test_the_join_normalises_both_sides():
    """"ZD-4491" against "4491" is the join that matched nothing."""
    assert contact_join_notes([{"ticket_id": "4491"}], [],
                              {"support_interaction_notes": [{"zd_ref": "ZD-4491"}]}) == []


def test_sp_records_are_joined_too():
    out = contact_join_notes([], [{"ticket_id": "7"}], {"sp_interaction_notes": {
        "raised": "Yes", "records": [{"zd_ref": "ZD-8", "summary": "wrong ticket"}]}})
    assert out and "could not be joined" in out[0]


def test_garbage_does_not_raise():
    for bad in (None, "nope", 42, {"support_interaction_notes": "nope"},
                {"sp_interaction_notes": []}):
        assert isinstance(contact_join_notes(None, None, bad), list)


def test_the_pipeline_puts_those_lines_in_the_trail():
    i = PIPE.find("contact_join_notes(support_frames")
    assert i > 0, "the pipeline never runs the join check"
    block = PIPE[i:i + 500]
    assert "confidence_trail.append" in block
    assert '"mark": "warn"' in block, "a failed join is not a step that succeeded"


def test_grouping_events_by_time_is_announced():
    """Grouping frames without a ticket id by a 30-minute window is a
    judgement, not a fact. An unannounced guess is how a guessed number
    becomes a trusted one."""
    out = contact_join_notes(
        [{"ticket_id": None, "time_sort": "2026-07-22T15:41:00"},
         {"ticket_id": None, "time_sort": "2026-07-22T15:49:00"}], [], {})
    assert any("30-minute window" in n for n in out)
    assert any("2 event(s) have no ticket id" in n for n in out)


def test_a_single_untracked_event_needs_no_announcement():
    """One event cannot have been grouped with anything, so saying so would be
    noise on every run that has one stray frame."""
    out = contact_join_notes([{"ticket_id": None}], [], {})
    assert not any("window" in n for n in out)


def test_fully_ticketed_frames_are_never_announced_as_grouped():
    out = contact_join_notes([{"ticket_id": "1"}, {"ticket_id": "2"}], [], {})
    assert out == []


# ── defect 5: the failure entry, driven ─────────────────────────────────────

def test_a_dead_run_records_a_sentence_and_keeps_the_raw():
    """The trail rendered 500 characters of SELECT straight into the dashboard.
    The sentence is what the reader acts on; the raw is kept because the only
    other copy lives in a log they cannot reach."""
    from server.pipeline import failure_entry

    class OperationalError(Exception):
        pass

    e = failure_entry(OperationalError(
        "(psycopg2.OperationalError) SSL connection has been closed unexpectedly "
        "[SQL: SELECT rca_drafts.id AS rca_drafts_id FROM rca_drafts]"))
    assert e["mark"] == "fail"
    assert "OperationalError" in e["title"]
    assert "connection dropped mid-run" in e["text"]
    assert "SELECT rca_drafts" not in e["text"], "the SQL is in the trail step itself"
    assert "SELECT rca_drafts" in e["raw"], "the raw error was discarded"


def test_the_raw_is_bounded():
    """A runaway exception must not become the row."""
    from server.pipeline import failure_entry
    e = failure_entry(Exception("x" * 9000))
    assert len(e["raw"]) <= 4000


def test_an_unrecognised_failure_still_gets_a_title_and_a_raw():
    from server.pipeline import failure_entry
    e = failure_entry(ValueError("something nobody has a sentence for"))
    assert e["title"].endswith("ValueError")
    assert e["raw"] and e["text"]


def test_the_caught_exception_never_reaches_the_trail_text():
    """Testing failure_entry() proves the shape; it does not prove the pipeline
    uses it. The positive form ("failure_entry( appears") is defeated by an
    unreachable line, so this is the negative one: the caught exception is only
    ever logged or handed to failure_entry — it is never formatted into a trail
    string, which no amount of dead code can satisfy.

    Not a count of fail entries: "BID — no 7-12 digit number found" is a
    legitimate fail that has nothing to do with an exception.
    """
    import re
    uses = [m.group(0) for m in re.finditer(r"[^\n]*\b_fatal\b[^\n]*", PIPE)]
    assert uses, "the fatal handler is gone"
    for line in uses:
        ok = ("except Exception as _fatal" in line
              or "log.exception" in line
              or "failure_entry(_fatal)" in line)
        assert ok, f"the exception is being formatted into the trail: {line.strip()[:90]}"


# ── an untraceable review sends the approved macro, unedited ────────────────
#
# Not as a tone reference — as the reply. There is nothing to personalise: we
# could not find the booking, so the reply is the one ask that applies to every
# such review. Having the model rewrite it produces an unapproved paraphrase of
# an approved reply, which is worse than either.

import re as _re

PIPE_SRC = open("server/pipeline.py", encoding="utf-8").read()


def _verbatim_block():
    i = PIPE_SRC.find("_verbatim = next((c for c in (canned_list or [])")
    assert i != -1, "the verbatim override is gone from the pipeline"
    return PIPE_SRC[i:i + 1400]


def test_the_macro_is_written_to_rca_v3_not_only_the_column():
    """_draft_dict reads the reply presence-based from rca_v3. Setting only the
    column leaves the card showing whatever the model wrote — which is the bug
    that let a stale 110-word reply override rule 20."""
    b = _verbatim_block()
    assert 'rca_v3["suggested_response"] = response_draft' in b


def test_the_first_name_token_is_filled():
    """`<first name>` renders as literal angle brackets on a public review
    page. It is the one token the macro is written to have filled."""
    b = _verbatim_block()
    assert '"<first name>"' in b


def test_a_missing_name_leaves_the_token_rather_than_guessing():
    """A wrong name on a public reply is worse than a placeholder an associate
    can see and complete."""
    b = _verbatim_block()
    assert "if _who:" in b, "the name is substituted unconditionally"


def test_only_the_state_selected_macro_is_used_verbatim():
    """Every other macro is a tone reference. If this took canned_list[0]
    unconditionally, every matched reply would be sent as boilerplate."""
    b = _verbatim_block()
    assert 'c.get("why")' in b, \
        "the verbatim path is not gated on the state-selected macro"
