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
    firing. A card cannot tell them apart, which is why this exists."""
    _seed(live_db, "tp_clean", rca_v3=_gaps(), actions_taken={})
    clean = _actions(live_db, "tp_clean", capsys)
    _seed(live_db, "tp_dropped", rca_v3=_gaps(UNSOURCED), actions_taken={})
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
    assert "Regenerate before reading" in out
    assert "found no unsolved gap" not in out


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
