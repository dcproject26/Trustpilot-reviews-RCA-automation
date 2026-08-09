"""The diagnostics must not answer from their own copy of the rule.

THE FAILURE THIS GUARDS, WHICH HAS ALREADY HAPPENED TWICE.

`trace_shaping.py` rebuilt the shaping path instead of driving it, and
reported a bug that was already fixed as still broken. A session went into
chasing it.

`trace_findings.py` re-derived the fold from `_SAME_FACT_OVERLAP` alone. After
the same-minute rule landed, a duplicate pair at 0.57 still printed "kept
apart" — the tracer disagreeing with the code it was pointed at, and the
reader has no way to know which one is lying.

A tracer that restates a threshold, a routing rule or a predicate is a SECOND
IMPLEMENTATION, and the whole reason to run one is that you do not trust your
reading of the first. These drive `main()` end to end against a seeded
database and check the output tells the cases apart — an empty section, a
dropped row and a broken rebuild must not print the same thing.
"""
import pytest


def _seed(db, rid, **cols):
    s = db.SessionLocal()
    s.add(db.Review(id=rid, rating=1, author="A", body_original="b",
                    status="draft"))
    s.add(db.RcaDraft(id=f"d_{rid}", review_id=rid, **cols))
    s.commit()
    s.close()


# ── Actions Taken ──────────────────────────────────────────────────────────

def _gaps(*rows):
    return {"what_went_wrong": {"guest_issues": [], "fixes": [],
                                "gaps": list(rows)}, "flags": []}


SOURCED = {"gap": "Chat miss — nobody followed up on the 08:30 request",
           "team": "CO", "source_ref": "ZD-34335318"}
UNSOURCED = {"gap": "Require agents to contact the guest proactively",
             "team": "CO", "source_ref": ""}


def _actions(db, rid, capsys):
    from scripts.trace_actions import main
    assert main([rid]) == 0
    return capsys.readouterr().out


def test_a_clean_case_and_a_dropped_gap_do_not_print_the_same_thing(live_db,
                                                                    capsys):
    """THE WHOLE POINT OF THE SECTION. Both leave the CO tab empty. One is a
    case with nothing outstanding; the other is the anti-hallucination gate
    firing. A card cannot tell them apart, which is why this exists.

    BOTH DRAFTS CARRY THE CURRENT STAMP, deliberately. "The case was clean" is
    a claim about what THIS prompt asked and got back; on a draft from an
    older build the same empty list means "not asked", and the tracer now says
    so instead. Seeding without a stamp would be testing the wrong branch."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_clean", rca_v3=_gaps(), actions_taken={},
          rca_prompt_version=RCA_PROMPT_VERSION)
    clean = _actions(live_db, "tp_clean", capsys)
    _seed(live_db, "tp_dropped", rca_v3=_gaps(UNSOURCED), actions_taken={},
          rca_prompt_version=RCA_PROMPT_VERSION)
    dropped = _actions(live_db, "tp_dropped", capsys)

    assert "found no unsolved gap" in clean
    assert "cites no ticket, contact or finding" in dropped
    assert "found no unsolved gap" not in dropped
    assert clean != dropped


def test_a_missing_gaps_key_is_not_reported_as_an_empty_case(live_db, capsys):
    """A draft generated before `gaps` existed has no key at all. Printing
    that as "no unsolved gap" would say this case was read and was clean."""
    _seed(live_db, "tp_pre", rca_v3={"what_went_wrong": {"guest_issues": [],
                                                         "fixes": []},
                                     "flags": []}, actions_taken={})
    out = _actions(live_db, "tp_pre", capsys)
    assert "ABSENT" in out
    assert "This is NOT an empty case" in out
    assert "found no unsolved gap" not in out
    # AND IT MUST SAY WHAT TO DO. The three tests below cover which diagnosis
    # is printed; this one only guards that SOME actionable next step is,
    # because the paragraph that named three causes and picked none was read
    # twice in a row and moved nobody forward.
    assert "Regenerate" in out


def test_a_stale_column_is_reported_as_drift_not_silently_reprinted(live_db,
                                                                    capsys):
    """`actions_taken` is the column Slack reads. When it disagrees with the
    gaps on the card, the post carries a routing the card does not show."""
    _seed(live_db, "tp_drift", rca_v3=_gaps(SOURCED),
          actions_taken={"co": ["a stale row nobody rebuilt"]})
    out = _actions(live_db, "tp_drift", capsys)
    assert "DRIFTED" in out
    assert "stored only: a stale row nobody rebuilt" in out


def test_an_in_step_column_says_so(live_db, capsys):
    """The inverse bug: a tracer that always cries drift is one nobody reads."""
    _seed(live_db, "tp_sync", rca_v3=_gaps(SOURCED),
          actions_taken={"co": [SOURCED["gap"]]})
    assert "In step" in _actions(live_db, "tp_sync", capsys)


def test_an_unrouted_gap_is_named_rather_than_left_off_the_tabs(live_db,
                                                               capsys):
    _seed(live_db, "tp_unrouted", rca_v3=_gaps(
        {"gap": "Reschedule automation has no alert on failure", "team": "",
         "source_ref": "ZD-33978941"}), actions_taken={})
    out = _actions(live_db, "tp_unrouted", capsys)
    assert "UNROUTED" in out
    assert "nobody picks those up" in out


# ── the events timeline ────────────────────────────────────────────────────

def _row(**kw):
    base = {"time": "02 Aug 09:11", "time_sort": "2026-08-02T03:41:00+00:00",
            "thread": "email", "actor": "co", "label": "L", "summary": "S",
            "ticket_id": "1", "is_internal": False, "internal_reason": ""}
    base.update(kw)
    return base


def _timeline(db, rid, capsys):
    from scripts.trace_timeline import main
    assert main([rid]) == 0
    return capsys.readouterr().out


def test_an_out_of_order_timeline_is_named_not_just_printed(live_db, capsys):
    """The list is ordered by `time_sort`, and a reader cannot see that key.
    A timeline sorted by its display string puts the review before the chat
    that caused it and looks perfectly ordered on screen."""
    _seed(live_db, "tp_tl_bad", timeline=[
        _row(time_sort="2026-08-02T10:06:00+00:00"),
        _row(time_sort="2026-08-02T03:41:00+00:00")])
    out = _timeline(live_db, "tp_tl_bad", capsys)
    assert "OUT OF ORDER" in out


def test_a_sorted_timeline_says_how_many_rows_it_could_place(live_db, capsys):
    _seed(live_db, "tp_tl_ok", timeline=[
        _row(time_sort="2026-08-02T03:41:00+00:00"),
        _row(time_sort="2026-08-02T10:06:00+00:00")])
    out = _timeline(live_db, "tp_tl_ok", capsys)
    assert "Sorted." in out and "2 of 2" in out
    assert "OUT OF ORDER" not in out


def test_a_row_with_no_sort_key_is_counted_rather_than_trusted(live_db, capsys):
    _seed(live_db, "tp_tl_nokey", timeline=[_row(), _row(time_sort="")])
    out = _timeline(live_db, "tp_tl_nokey", capsys)
    assert "NO SORT KEY" in out
    assert "1 row(s) carry NO sort key" in out


def test_zero_internal_rows_is_reported_as_expected_not_as_a_warning(live_db,
                                                                     capsys):
    """THE INVERSE BUG, caught on real data. This line used to read zero as
    "the fetch gate ate them" and told the reader to go checking — on a card
    where every internal note had been correctly PROMOTED.

    `note_disposition` clears `is_internal` on each note it keeps, which is
    what moves a booking fact out from behind the toggle. Zero left marked is
    the healthy state. Making a working card look faulty is as bad as the
    reverse, and costs the same afternoon."""
    _seed(live_db, "tp_tl_noint", timeline=[_row()])
    out = _timeline(live_db, "tp_tl_noint", capsys)
    assert "Zero is the EXPECTED state" in out
    assert "gate ate" not in out and "too wide" not in out


def test_a_row_still_marked_internal_is_named_as_dropped_administration(
        live_db, capsys):
    """What stays marked is what was DROPPED, and that is the only thing this
    count can honestly report."""
    _seed(live_db, "tp_tl_dropped", timeline=[
        _row(is_internal=True, internal_reason="ticket admin")])
    out = _timeline(live_db, "tp_tl_dropped", capsys)
    assert "1 row(s) are still marked internal" in out
    assert "Zero is the EXPECTED state" not in out


def test_an_internal_note_is_shown_with_the_reason_it_was_marked(live_db,
                                                                 capsys):
    _seed(live_db, "tp_tl_int", timeline=[
        _row(is_internal=True, internal_reason="agent note")])
    out = _timeline(live_db, "tp_tl_int", capsys)
    assert "INTERNAL:agent note" in out
    assert "Check trace_notes.py" not in out


def test_a_guest_contact_that_reaches_no_panel_is_visible(live_db, capsys):
    """A chat transcript extracted and then rejected by `is_conversation` is a
    support interaction that was found and dropped — the exact bug, and it
    leaves the panel looking like a case with no contact."""
    _seed(live_db, "tp_tl_nocontact", timeline=[
        _row(is_internal=True, internal_reason="selenium", actor="system")])
    out = _timeline(live_db, "tp_tl_nocontact", capsys)
    assert "0 row(s) pass `is_conversation`" in out
    assert "that is the bug, not an empty case" in out


def test_a_bookend_stamped_row_announces_the_judgement(live_db, capsys):
    """"at booking" is not a record of a time. It is a decision about where a
    timeless row belongs, and a decision has to say so.

    SURVIVED A MUTATION at `stamp = False`. The footer prints "N row(s) were
    BOOKEND-STAMPED" unconditionally, so a bare `in` check matched the footer
    and passed while every row went unmarked. Both the count and the marker
    are asserted, and a dated row must not be marked at all."""
    _seed(live_db, "tp_tl_stamp", timeline=[_row(time="at booking"), _row()])
    out = _timeline(live_db, "tp_tl_stamp", capsys)
    assert "1 row(s) were BOOKEND-STAMPED" in out
    marked = [l for l in out.splitlines() if "BOOKEND-STAMPED" in l
              and "row(s) were" not in l]
    assert len(marked) == 1, marked


def test_an_empty_timeline_does_not_read_as_a_quiet_booking(live_db, capsys):
    _seed(live_db, "tp_tl_empty", timeline=[])
    out = _timeline(live_db, "tp_tl_empty", capsys)
    assert "EMPTY" in out and "shaping call that failed" in out


# ── the not-found path names what would work ───────────────────────────────

@pytest.mark.parametrize("mod", ["trace_timeline", "trace_actions"])
def test_a_missing_id_names_the_ids_that_are_there(live_db, capsys, mod):
    """"not found" alone is the sentence a broken lookup gives. `show_draft
    --bid` keyed on the wrong field and said exactly that for months."""
    import importlib
    _seed(live_db, "tp_here", timeline=[_row()], rca_v3=_gaps())
    main = importlib.import_module(f"scripts.{mod}").main
    assert main(["tp_not_a_real_id"]) == 1
    out = capsys.readouterr().out
    assert "Drafts that are here" in out and "tp_here" in out


# ── neither tracer carries its own copy of a rule ──────────────────────────
#
# NEGATIVE SOURCE ASSERTIONS. Per the working rules these are the one source
# check that holds: unreachable code cannot make a string appear nowhere. The
# point is that a tracer must IMPORT the predicate, so it moves when the rule
# moves — a literal threshold here is a second implementation that will drift
# silently the next time a number changes.

@pytest.mark.parametrize("path", ["scripts/trace_timeline.py",
                                  "scripts/trace_actions.py",
                                  "scripts/trace_findings.py"])
def test_no_tracer_hardcodes_a_threshold(path):
    """SURVIVED A MUTATION. The check was a regex for `>= 0.35`, so it caught
    a restated COMPARISON and missed `bar = 0.35 if same_minute else 0.6` —
    an assignment, and the exact shape the real drift took.

    Parsed instead: any float literal in the file is a restated rule, with
    argparse `default=` excepted because a knob is not a rule."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    allowed = {id(kw.value) for node in ast.walk(tree)
               if isinstance(node, ast.Call)
               for kw in node.keywords if kw.arg == "default"}
    hits = [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, float)
            and id(n) not in allowed]
    assert not hits, \
        f"{path} carries literal threshold(s) {hits} — import them, so they " \
        f"move when the rule moves"


@pytest.mark.parametrize("path,names", [
    ("scripts/trace_findings.py", ("_SAME_MINUTE_OVERLAP", "_finding_minute")),
    ("scripts/trace_timeline.py", ("is_conversation",)),
    ("scripts/trace_actions.py", ("actions_from_gaps", "ACTION_TEAMS")),
])
def test_each_tracer_imports_the_rule_it_reports_on(path, names):
    """PARSED, not string-matched. `"import" in src` is true of every file in
    this repo and asserts nothing; the name has to appear in an actual import
    statement, and must not be defined locally alongside it."""
    import ast
    tree = ast.parse(open(path, encoding="utf-8").read())
    imported, defined = set(), set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.update(al.asname or al.name for al in node.names)
        elif isinstance(node, ast.Import):
            imported.update((al.asname or al.name).split(".")[0]
                            for al in node.names)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
        elif isinstance(node, ast.Assign):
            defined.update(t.id for t in node.targets
                           if isinstance(t, ast.Name))
    for n in names:
        assert n in imported, \
            f"{path} does not import {n} — it cannot move when the rule moves"
        assert n not in defined, \
            f"{path} defines its own {n} — that is a second implementation"


# ── "regenerate it" is useless advice to someone who just did ──────────────
#
# The trace was run twice on one card and printed the same ABSENT paragraph
# both times. The paragraph was true and unactionable: it named three possible
# causes and gave the reader no way to tell which.
#
# `validate` writes `gaps` unconditionally, so ABSENT proves the blob did not
# come from the current validate. The stored prompt stamp splits the rest: a
# stamp older than the running one is a stale draft, a MATCHING stamp is not —
# it means the code ran and the key still went missing.

def test_a_stale_draft_is_told_apart_from_a_current_one(live_db, capsys):
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_stale",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": []}},
          actions_taken={}, rca_prompt_version="rca-v4+deadbeef")
    out = _actions(live_db, "tp_stale", capsys)
    assert "predates the running prompt" in out
    assert RCA_PROMPT_VERSION in out, "the reader cannot compare what is hidden"


def test_a_current_stamp_with_no_gaps_points_at_the_code_not_the_draft(
        live_db, capsys):
    """THE CASE THAT MATTERS. Telling someone to regenerate a draft the
    current build already produced sends them round the same loop."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_current",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": []}},
          actions_taken={}, rca_prompt_version=RCA_PROMPT_VERSION)
    out = _actions(live_db, "tp_current", capsys)
    assert "MATCHES the running prompt" in out
    assert "keeping raw output" in out
    assert "predates the running prompt" not in out


def test_an_unstamped_draft_says_so_rather_than_comparing_nothing(live_db,
                                                                  capsys):
    _seed(live_db, "tp_nostamp",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": []}},
          actions_taken={})
    out = _actions(live_db, "tp_nostamp", capsys)
    assert "NO prompt stamp at all" in out


def test_a_stored_empty_list_is_not_reported_as_absent(live_db, capsys):
    """The inverse. [] is the model answering "nothing outstanding", and
    sending the reader to regenerate a correct card is the same waste in the
    other direction."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_emptygaps", rca_v3=_gaps(), actions_taken={},
          rca_prompt_version=RCA_PROMPT_VERSION)
    out = _actions(live_db, "tp_emptygaps", capsys)
    assert "found no unsolved gap" in out
    assert "ABSENT" not in out


# ── a stored draft read against rules it was never generated under ─────────
#
# THE LOOP THIS ENDS. `gaps: []` was printed three runs running as "the model
# was asked and found no unsolved gap". Twice that was false: the draft came
# from a prompt whose JSON skeleton had no `gaps` in it at all, so the model
# answered a question nobody put to it. A clean case and an unasked one wrote
# the same sentence, which is rule 1 of CLAUDE.md with the stakes reversed —
# the healthy-looking output was the broken one.
#
# The stamp is content-addressed, so it moves whenever the prompt body moves.
# That makes "stale" mean something narrower and more useful than "old": the
# model was asked DIFFERENT QUESTIONS, so its answers cannot be read here.

import pytest as _pytest


@_pytest.mark.parametrize("mod,cols", [
    ("trace_actions", {"rca_v3": {"what_went_wrong": {"gaps": []}}}),
    ("trace_findings", {"rca_v3": {"what_went_wrong": {"case_findings": []}}}),
    ("trace_timeline", {"timeline": []}),
])
def test_every_tracer_banners_a_draft_from_an_older_prompt(live_db, capsys,
                                                           mod, cols):
    import importlib
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, f"tp_stale_{mod}", rca_prompt_version="rca_v4+0ldbu1ld",
          **cols)
    importlib.import_module(f"scripts.{mod}").main([f"tp_stale_{mod}"])
    out = capsys.readouterr().out
    assert "PREDATES THE RUNNING PROMPT" in out, out
    assert "rca_v4+0ldbu1ld" in out and RCA_PROMPT_VERSION in out, \
        "a verdict the reader cannot check is one they have to take on trust"
    assert "REGENERATE THE RCA" in out


@_pytest.mark.parametrize("mod,cols", [
    ("trace_actions", {"rca_v3": {"what_went_wrong": {"gaps": []}}}),
    ("trace_findings", {"rca_v3": {"what_went_wrong": {"case_findings": []}}}),
    ("trace_timeline", {"timeline": []}),
])
def test_no_tracer_banners_a_current_draft(live_db, capsys, mod, cols):
    """THE INVERSE. A banner on every card is a banner nobody reads, and it
    would make a correct run look suspect."""
    import importlib
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, f"tp_cur_{mod}", rca_prompt_version=RCA_PROMPT_VERSION,
          **cols)
    importlib.import_module(f"scripts.{mod}").main([f"tp_cur_{mod}"])
    out = capsys.readouterr().out
    assert "PREDATES" not in out and "NO PROMPT STAMP" not in out, out


def test_an_unstamped_draft_gets_its_own_banner(live_db, capsys):
    import importlib
    _seed(live_db, "tp_nostamp_banner", timeline=[])
    importlib.import_module("scripts.trace_timeline").main(["tp_nostamp_banner"])
    out = capsys.readouterr().out
    assert "NO PROMPT STAMP" in out
    assert "PREDATES" not in out, "two different states, two different words"


def test_an_empty_gap_list_on_a_stale_draft_is_not_called_a_real_answer(
        live_db, capsys):
    """THE SENTENCE THAT WAS WRONG. Verbatim: "The model was asked and found
    no unsolved gap. That is a real answer." It was neither.

    ASSERTED ON THE BRANCH'S OWN WORDS. The first version of this test looked
    for "'not asked', not 'nothing found'" — which the BANNER also prints, so
    it matched there and passed with the branch inverted. A mutation flipping
    the condition to `if True` survived the whole suite. Both branches now
    carry a phrase the other does not."""
    _seed(live_db, "tp_stale_empty", rca_v3=_gaps(),
          rca_prompt_version="rca_v4+0ldbu1ld", actions_taken={})
    out = _actions(live_db, "tp_stale_empty", capsys)
    assert "see the banner above" in out, out
    assert "from THIS prompt build" not in out
    assert "found no unsolved gap — a real answer" not in out


def test_an_empty_gap_list_on_a_current_draft_still_is_one(live_db, capsys):
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_cur_empty", rca_v3=_gaps(),
          rca_prompt_version=RCA_PROMPT_VERSION, actions_taken={})
    out = _actions(live_db, "tp_cur_empty", capsys)
    assert "from THIS prompt build" in out
    assert "not asked" not in out


# ── the state function itself ──────────────────────────────────────────────

def test_the_stamp_states_are_three_and_distinct():
    from server.prompts import prompt_stamp_state, RCA_PROMPT_VERSION
    assert prompt_stamp_state(RCA_PROMPT_VERSION) == "current"
    assert prompt_stamp_state("rca_v4+something_else") == "stale"
    assert prompt_stamp_state("") == "unstamped"
    assert prompt_stamp_state(None) == "unstamped"
    assert prompt_stamp_state("   ") == "unstamped", \
        "whitespace is not a stamp — it would read as a version nobody wrote"


# ── clearing rows no gap explains ──────────────────────────────────────────
#
# I TOLD THE USER THESE WOULD CLEAR THEMSELVES. They will not, and the reason
# is worth writing down. `hand_typed_actions` subtracts what the PREVIOUS gaps
# explain and treats the remainder as a person's. Once gaps are stored that is
# right. It does not reach BACKWARDS: rows written before gaps existed are
# neither derived-from-current-gaps nor hand-typed — they are model output
# from the old fixes-derived section — and from the run after gaps first land
# they are carried forward as hand-typed, confidently and forever.
#
# Nothing in the data separates them from a row somebody typed that afternoon.
# So the code does not choose; this prints what it would remove and removes
# nothing until told.

def _clear(db, rid, capsys, *flags):
    from scripts.clear_unattributed_actions import main
    rc = main([rid, *flags])
    return rc, capsys.readouterr().out


def test_a_dry_run_names_every_row_and_writes_nothing(live_db, capsys):
    _seed(live_db, "tp_cl", rca_v3=_gaps(SOURCED),
          actions_taken={"co": [SOURCED["gap"], "a stale recommendation"]})
    rc, out = _clear(live_db, "tp_cl", capsys)
    assert rc == 0
    assert "a stale recommendation" in out
    assert "DRY RUN" in out
    s = live_db.SessionLocal()
    row = s.query(live_db.RcaDraft).filter_by(review_id="tp_cl").first()
    still = dict(row.actions_taken or {}); s.close()
    assert "a stale recommendation" in still["co"], still


def test_apply_removes_exactly_those_rows_and_keeps_the_explained_one(live_db,
                                                                      capsys):
    _seed(live_db, "tp_cl2", rca_v3=_gaps(SOURCED),
          actions_taken={"co": [SOURCED["gap"], "a stale recommendation"]})
    rc, out = _clear(live_db, "tp_cl2", capsys, "--apply")
    assert rc == 0 and "Removed 1 row(s)" in out
    s = live_db.SessionLocal()
    row = s.query(live_db.RcaDraft).filter_by(review_id="tp_cl2").first()
    left = dict(row.actions_taken or {}); s.close()
    assert left["co"] == [SOURCED["gap"]], left


def test_it_refuses_when_there_are_no_stored_gaps(live_db, capsys):
    """THE DANGEROUS CASE. With no gaps every row looks unexplained, so this
    would clear the whole column — including a row somebody typed. Refusing
    and naming the fix beats a --apply that empties the tab."""
    _seed(live_db, "tp_cl3",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": []}},
          actions_taken={"co": ["something a person typed"]})
    rc, out = _clear(live_db, "tp_cl3", capsys, "--apply")
    assert rc == 1
    assert "Regenerate the RCA first" in out
    s = live_db.SessionLocal()
    row = s.query(live_db.RcaDraft).filter_by(review_id="tp_cl3").first()
    left = dict(row.actions_taken or {}); s.close()
    assert left["co"] == ["something a person typed"], left


def test_a_clean_column_says_so_rather_than_printing_an_empty_list(live_db,
                                                                   capsys):
    _seed(live_db, "tp_cl4", rca_v3=_gaps(SOURCED),
          actions_taken={"co": [SOURCED["gap"]]})
    rc, out = _clear(live_db, "tp_cl4", capsys)
    assert rc == 0 and "Nothing to clear" in out


# ── the whole card, end to end ─────────────────────────────────────────────
#
# Every other tracer reads ONE section. That found real bugs and kept missing
# a class: a section fine in the data that never reaches the screen, or one
# reaching it from a key nothing writes. Both draw an empty block, and an
# empty block is what a clean case looks like.
#
# THREE THINGS MUST NOT PRINT ALIKE, and the seeds below are the three:
#   ok           content, and a renderer reads it
#   empty        the key is there and holds nothing — often legitimate
#   KEY ABSENT   the client asks and gets undefined

def _card(db, rid, capsys, *flags):
    from scripts.trace_card import main
    assert main([rid, *flags]) == 0
    return capsys.readouterr().out


def _full_v3():
    from server.services.rca_v4_validate import validate
    v3, _ = validate({
        "stated_issue": "Pickup time changed without notice",
        "what_went_wrong": {
            "case_findings": [{"text": "Booking created for 08:30",
                               "source": "booking", "time": "21 Jul 15:28"}],
            "guest_issues": [], "fixes": [],
            "gaps": [{"gap": "Guest's request was closed unresolved",
                      "team": "CO", "source_ref": "ZD-34335318"}]},
        "flags": []})
    v3["suggested_response"] = "Thank you for flagging this."
    return v3


def test_a_present_section_a_blank_one_and_a_missing_key_read_differently(
        live_db, capsys):
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_full", rca_v3=_full_v3(),
          actions_taken={"co": ["Guest's request was closed unresolved"]},
          rca_prompt_version=RCA_PROMPT_VERSION)
    out = _card(live_db, "tp_card_full", capsys)
    assert "§1 case findings" in out
    lines = {l.split()[0] + " " + l.split()[1]: l for l in out.splitlines()
             if l.startswith("  §") or "Actions Taken" in l}
    assert "KEY ABSENT" not in out, out


def test_a_pre_gaps_draft_says_KEY_ABSENT_not_empty(live_db, capsys):
    """The shape every card was in before gaps were stored. "empty" would say
    the model was asked and found nothing."""
    _seed(live_db, "tp_card_old",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": []}},
          actions_taken={})
    out = _card(live_db, "tp_card_old", capsys)
    assert "KEY ABSENT" in out
    assert "KEYS THE PROJECTION DOES NOT CARRY" in out
    assert "rca_v3.what_went_wrong.gaps" in out


def test_an_empty_actions_column_is_not_counted_as_five_tabs(live_db, capsys):
    """THE BUG IN THIS FILE. `actions_taken` is a dict of five tabs, and a
    card with nothing routed still has all five keys — so `len()` printed
    "5  ok" for a tab strip holding nothing. This file reporting a healthy
    card at the exact moment the section was empty is the defect it exists to
    catch."""
    _seed(live_db, "tp_card_notabs",
          rca_v3={"what_went_wrong": {"guest_issues": [], "fixes": [],
                                      "gaps": []}},
          actions_taken={})
    out = _card(live_db, "tp_card_notabs", capsys)
    row = next(l for l in out.splitlines() if "Actions Taken tabs" in l)
    assert " 0 " in row and "empty" in row, row


def test_a_populated_tab_still_counts_its_rows(live_db, capsys):
    """The inverse: undercounting to zero would make a working strip look
    broken."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_tabs", rca_v3=_full_v3(),
          actions_taken={"co": ["a", "b"], "sp": ["c"]},
          rca_prompt_version=RCA_PROMPT_VERSION)
    out = _card(live_db, "tp_card_tabs", capsys)
    row = next(l for l in out.splitlines() if "Actions Taken tabs" in l)
    assert " 3 " in row and "ok" in row, row


def test_a_stale_draft_banners_here_too(live_db, capsys):
    _seed(live_db, "tp_card_stale", rca_v3=_full_v3(),
          rca_prompt_version="rca_v4+0ldbu1ld")
    out = _card(live_db, "tp_card_stale", capsys)
    assert "NOT WRITTEN BY THE RUNNING PROMPT" in out
    assert "'not asked', not 'nothing found'" in out


def test_the_trail_warnings_are_surfaced_not_buried(live_db, capsys):
    """A `warn` is where the code changed what the model said. Those lines
    are what explain a thin section, and they live in a column nobody opens."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_warn", rca_v3=_full_v3(),
          rca_prompt_version=RCA_PROMPT_VERSION,
          # THE REAL SHAPE, read off api.py rather than invented. The first
          # version of this test seeded {"status", "detail"} — keys nothing
          # writes — so the tracer's own wrong key list matched it and the
          # pair passed together. On a live card carrying nine warnings it
          # printed "No warnings."
          confidence_trail=[{"mark": "warn",
                             "text": "<strong>RCA</strong> — 1 gap cited a "
                                     "description"},
                            {"mark": "pass", "text": "booking confirmed"}])
    out = _card(live_db, "tp_card_warn", capsys)
    assert "2 entries, 0 fail, 1 warn" in out
    assert "1 gap cited a description" in out
    assert "booking confirmed" not in out, "a pass is not a warning"
    assert "<strong>" not in out, "markup belongs in the card, not the trace"


def test_no_warnings_says_so_rather_than_printing_nothing(live_db, capsys):
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_nowarn", rca_v3=_full_v3(),
          rca_prompt_version=RCA_PROMPT_VERSION,
          confidence_trail=[{"mark": "pass", "text": "booking confirmed"}])
    out = _card(live_db, "tp_card_nowarn", capsys)
    assert "No warnings" in out


def test_verbose_prints_the_values_and_the_default_does_not(live_db, capsys):
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_v", rca_v3=_full_v3(),
          rca_prompt_version=RCA_PROMPT_VERSION)
    plain = _card(live_db, "tp_card_v", capsys)
    assert "Booking created for 08:30" not in plain
    assert "--verbose" in plain
    rich = _card(live_db, "tp_card_v", capsys, "--verbose")
    assert "Booking created for 08:30" in rich


def test_a_trail_entry_whose_mark_is_unreadable_is_counted_not_swallowed(
        live_db, capsys):
    """THE BUG THIS FILE SHIPPED. trace_card read `status`; the trail is
    written with `mark`. So it printed "No warnings. Nothing was coerced and
    nothing was reported as undone." on a card carrying NINE warnings, one of
    them "the lookup never ran".

    The tests passed because they seeded the invented shape — a closed loop
    validating the fiction. The fix is not just the right key: an entry the
    file cannot classify is named, so the next shape change says so instead of
    reporting a clean trail."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_odd", rca_v3=_full_v3(),
          rca_prompt_version=RCA_PROMPT_VERSION,
          confidence_trail=[{"verdict": "warn", "message": "a new shape"}])
    out = _card(live_db, "tp_card_odd", capsys)
    assert "carry no mark this file recognises" in out
    assert "No warnings" not in out, \
        "an unreadable trail must never read as a clean one"


def test_the_real_production_shape_is_the_one_that_is_read(live_db, capsys):
    """`mark` is what api.py appends. Asserted here so a rename breaks this
    rather than silently emptying the section."""
    from server.prompts import RCA_PROMPT_VERSION
    _seed(live_db, "tp_card_mark", rca_v3=_full_v3(),
          rca_prompt_version=RCA_PROMPT_VERSION,
          confidence_trail=[
              {"mark": "fail", "text": "BID — no 7–12 digit number found"},
              {"mark": "warn", "text": "Zendesk was not searched"},
              {"mark": "pass", "text": "Author parsed"}])
    out = _card(live_db, "tp_card_mark", capsys)
    assert "3 entries, 1 fail, 1 warn" in out
    assert "Zendesk was not searched" in out
    assert "BID — no 7–12 digit number found" in out, \
        "a fail is louder than a warn and must not be dropped"
    assert "Author parsed" not in out


def test_api_writes_the_key_this_file_reads():
    """Parsed, not asserted from memory. Every trail dict api.py appends is
    checked for the mark key trace_card looks for — the two drifting apart is
    exactly how this broke."""
    import ast
    import inspect
    from server import api
    tree = ast.parse(inspect.getsource(api))
    marks = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            keys = {k.value for k in n.keys
                    if isinstance(k, ast.Constant) and isinstance(k.value, str)}
            if "mark" in keys or "status" in keys:
                marks |= keys & {"mark", "status"}
    assert marks, "no trail-shaped dict found in api.py — the parse is broken"
    from scripts.trace_card import main  # noqa: F401  (import must not fail)
    src = open("scripts/trace_card.py", encoding="utf-8").read()
    for m in marks:
        assert f'"{m}"' in src, \
            f"api.py writes {m!r} on trail entries and trace_card never reads it"
