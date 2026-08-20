"""The audit scores the live classifier against sheet labels — and the whole
reason it exists is to keep two distinctions from collapsing:

  * scored-and-wrong versus could-not-be-scored (model down / no label)
  * a model miss versus a label with no home in the taxonomy

So the tests drive the scoring and the write-back plan with hand-built rows and
assert the BEHAVIOUR, never that a string appears in the source. The sheet IO
and the model are not touched here; both are injected in the tool, and this is
the layer that can be exercised without either.
"""
from datetime import datetime

import pytest

from server.services import classifier_audit as CA


NOW = datetime(2026, 8, 19, 12, 0)


# ── column detection speaks when it finds nothing ───────────────────────────

def test_columns_are_found_by_name_not_position():
    header = ["Review Text", "L1", "L2", "Sub Theme", "review_id"]
    cols, problems = CA.detect_columns(header)
    assert cols["review"] == 0
    assert cols["l1"] == 1 and cols["l2"] == 2
    assert cols["sub_theme"] == 3
    assert cols["review_id"] == 4
    assert not problems


def test_a_header_with_no_label_column_says_so_rather_than_scoring_zero():
    """THE POINT, and rule 1. A sheet of reviews with no l1 column and an empty
    sheet both score nothing; only the problem list can tell them apart."""
    cols, problems = CA.detect_columns(["review", "l2", "sub_theme"])
    assert "review" in cols
    assert any("no l1 column" in p for p in problems), problems


def test_no_review_and_no_id_is_named_as_nothing_to_classify():
    cols, problems = CA.detect_columns(["l1", "l2"])
    assert any("nothing to send to the classifier" in p for p in problems)


def test_a_short_row_does_not_crash_on_a_blank_trailing_sub_theme():
    """Sheets omits trailing empty cells, so a labelled review with no
    sub-theme arrives as a row SHORTER than the header. That is the commonest
    row, not an error."""
    cols, _ = CA.detect_columns(["review", "l1", "l2", "sub_theme"])
    t = CA.truth_of(["it was late", "Operations Issue", "Timing"], cols)
    assert t["sub_theme"] == ""
    assert t["l2"] == "Timing"


# ── scoring keeps "not scored" distinct from "wrong" ────────────────────────

def test_a_row_the_model_never_reached_scores_none_not_false():
    """A failed model call is an ABSENCE of a score, not a wrong answer. If it
    came back False it would drag the accuracy down as if the model had erred,
    which is the exact inversion CLAUDE.md rule 1 warns about."""
    t = {"l1": "Operations Issue", "l2": "Timing", "sub_theme": "",
         "review": "x", "review_id": "1", "booking": "", "timeline": ""}
    v = CA.score_one(t, {}, ran=False, fail_reason="timeout")
    assert v["l1_ok"] is None and v["scored"] is False
    assert v["note"] == "timeout"
    assert v["miss_bucket"] == ""      # not counted as a miss


def test_an_unlabelled_level_is_not_scored_even_when_the_model_answered():
    t = {"l1": "", "l2": "", "sub_theme": "",
         "review": "x", "review_id": "1", "booking": "", "timeline": ""}
    v = CA.score_one(t, {"l1": "Operations Issue", "l2": "Timing"})
    assert v["scored"] is False
    assert v["l1_ok"] is None


def test_a_right_answer_scores_true_all_the_way_down():
    t = {"l1": "Supply Partner Issue", "l2": "Guide Issues",
         "sub_theme": "A. Guide No Show", "review": "x", "review_id": "1",
         "booking": "", "timeline": ""}
    p = {"l1": "supply partner issue", "l2": "Guide Issues ",
         "sub_theme": "A. Guide No Show"}
    v = CA.score_one(t, p)
    assert v["l1_ok"] and v["l2_ok"] and v["sub_ok"]
    assert v["miss_bucket"] == CA.BUCKET_NONE


def test_a_wrong_l1_stops_l2_and_sub_being_scored():
    """L2 under the wrong L1 is meaningless — "Timing" is a different thing in
    every L1 it appears under — so it is not graded, not graded-wrong."""
    t = {"l1": "Supply Partner Issue", "l2": "Guide Issues",
         "sub_theme": "A. Guide No Show", "review": "x", "review_id": "1",
         "booking": "", "timeline": ""}
    v = CA.score_one(t, {"l1": "Operations Issue", "l2": "Guide Issues",
                         "sub_theme": "A. Guide No Show"})
    assert v["l1_ok"] is False
    assert v["l2_ok"] is None and v["sub_ok"] is None
    assert v["miss_bucket"] == CA.BUCKET_L1L2


# ── the bucket points at where the fix lives ────────────────────────────────

def test_a_wrong_subtheme_under_a_real_framework_is_an_example_gap():
    """Both labels are REAL sub-themes in a real framework — derived from the
    taxonomy, not guessed — so the miss is genuinely the model picking the
    wrong one of two valid answers: an example gap, not a hole in the map."""
    from server.taxonomy import (sub_theme_framework, has_sub_theme_framework,
                                 L2_OPTIONS)
    pair = None
    for l1, opts in L2_OPTIONS.items():
        for l2 in opts:
            fw = sub_theme_framework(l1, l2) if has_sub_theme_framework(l1, l2) else None
            if fw and len(fw["sub_themes"]) >= 2:
                pair = (l1, l2, fw)
                break
        if pair:
            break
    l1, l2, fw = pair
    truth_label = f"{fw['sub_themes'][0][0]}. {fw['sub_themes'][0][1]}"
    pred_label = f"{fw['sub_themes'][1][0]}. {fw['sub_themes'][1][1]}"
    t = {"l1": l1, "l2": l2, "sub_theme": truth_label,
         "review": "x", "review_id": "1", "booking": "", "timeline": ""}
    v = CA.score_one(t, {"l1": l1, "l2": l2, "sub_theme": pred_label})
    assert v["sub_ok"] is False
    assert v["miss_bucket"] == CA.BUCKET_SUB


def test_a_label_the_taxonomy_cannot_hold_is_a_taxonomy_gap_not_a_miss():
    """The distinction that sends the fix to the right person. If (l1,l2) has no
    sub-theme framework, CX's sub-theme label can never validate and the model
    could never have produced it — so it is a hole in the map, not a model
    error, and must not read as one."""
    from server.taxonomy import has_sub_theme_framework, L2_OPTIONS
    # find an (l1,l2) with NO framework
    target = None
    for l1, opts in L2_OPTIONS.items():
        for l2 in opts:
            if not has_sub_theme_framework(l1, l2):
                target = (l1, l2)
                break
        if target:
            break
    if not target:
        pytest.skip("every l1/l2 now has a framework — nothing to test here")
    l1, l2 = target
    t = {"l1": l1, "l2": l2, "sub_theme": "Some Label CX Invented",
         "review": "x", "review_id": "1", "booking": "", "timeline": ""}
    v = CA.score_one(t, {"l1": l1, "l2": l2, "sub_theme": ""})
    assert v["miss_bucket"] == CA.BUCKET_TAXONOMY


def test_a_framework_that_rejects_cxs_label_is_a_validator_gap():
    """A real framework exists but the exact label CX uses is not in it. The
    model cannot emit it and survive validation, so the fix is the sheet or the
    framework — never the prompt. This was a live case: "A. AG Not Sent / Not
    Received Issues" against a framework that spells it without "Issues"."""
    from server.taxonomy import has_sub_theme_framework, is_valid_sub_theme, L2_OPTIONS
    target = None
    for l1, opts in L2_OPTIONS.items():
        for l2 in opts:
            if has_sub_theme_framework(l1, l2):
                target = (l1, l2)
                break
        if target:
            break
    l1, l2 = target
    bogus = "Zzz Not A Real Sub Theme In This Framework"
    assert not is_valid_sub_theme(l1, l2, bogus)
    t = {"l1": l1, "l2": l2, "sub_theme": bogus,
         "review": "x", "review_id": "1", "booking": "", "timeline": ""}
    v = CA.score_one(t, {"l1": l1, "l2": l2, "sub_theme": ""})
    assert v["miss_bucket"] == CA.BUCKET_VALIDATOR


# ── the summary reports denominators and separates the un-runnable ──────────

def test_the_rate_is_over_scorable_rows_and_carries_its_denominator():
    scored = [
        {"scored": True, "l1_ok": True,  "l2_ok": True,  "sub_ok": None,
         "miss_bucket": ""},
        {"scored": True, "l1_ok": True,  "l2_ok": False, "sub_ok": None,
         "miss_bucket": CA.BUCKET_L1L2},
        {"scored": True, "l1_ok": False, "l2_ok": None,  "sub_ok": None,
         "miss_bucket": CA.BUCKET_L1L2},
        {"scored": False, "note": "timeout", "l1_ok": None,
         "l2_ok": None, "sub_ok": None, "miss_bucket": ""},
    ]
    s = CA.summarize(scored)
    assert s["l1"]["of"] == 3 and s["l1"]["hits"] == 2
    assert s["l1"]["pct"] == pytest.approx(66.7, abs=0.1)
    # L2 scorable only where L1 was right → 2 rows, 1 hit
    assert s["l1_l2"]["of"] == 2 and s["l1_l2"]["hits"] == 1
    assert s["rows_failed"] == 1 and s["failures"] == ["timeout"]
    assert s["rows_scored"] == 3


def test_a_level_nobody_labelled_reports_none_not_a_hundred_percent():
    """No sub-theme labels at all must read as "not scored", never as 0/0 = a
    silent 100% or a silent 0%."""
    scored = [{"scored": True, "l1_ok": True, "l2_ok": True, "sub_ok": None,
               "miss_bucket": ""}]
    s = CA.summarize(scored)
    assert s["sub"]["pct"] is None
    assert s["sub"]["of"] == 0


def test_the_failures_do_not_count_as_zero_misses():
    """A whole batch the model could not reach must not summarise as a clean
    run. rows_scored is 0 and rows_failed carries the truth."""
    scored = [{"scored": False, "note": "model down", "l1_ok": None,
               "l2_ok": None, "sub_ok": None, "miss_bucket": ""}
              for _ in range(5)]
    s = CA.summarize(scored)
    assert s["rows_scored"] == 0
    assert s["rows_failed"] == 5
    assert s["l1"]["pct"] is None
    assert not s["miss_buckets"]


# ── the write-back plan ─────────────────────────────────────────────────────

def test_result_columns_land_after_the_input_and_are_named():
    header = ["review", "l1", "l2", "sub_theme"]
    cols = CA.result_columns(header)
    assert cols["pred_l1"] == 4
    assert cols["audited_at"] == 4 + len(CA.RESULT_COLS) - 1


def test_a_second_run_reuses_the_first_runs_columns():
    """THE POINT of result_columns. Re-auditing must overwrite the previous
    predictions, not append a second pred_l1 nobody can tell from the first."""
    header = ["review", "l1", "l2", "sub_theme"] + CA.RESULT_COLS
    cols = CA.result_columns(header)
    assert cols["pred_l1"] == 4          # where it already is, not 13
    assert max(cols.values()) < len(header)


def test_col_letter_crosses_z():
    assert CA._col_letter(0) == "A"
    assert CA._col_letter(25) == "Z"
    assert CA._col_letter(26) == "AA"
    assert CA._col_letter(27) == "AB"


def test_the_writeback_carries_the_header_and_one_span_per_row():
    header = ["review", "l1", "l2", "sub_theme"]
    v_ok = {"l1_ok": True, "l2_ok": True, "sub_ok": None,
            "miss_bucket": "", "note": ""}
    v_fail = {"l1_ok": None, "l2_ok": None, "sub_ok": None,
              "miss_bucket": "", "note": "timeout"}
    rows = [(v_ok, {"l1": "Operations Issue", "l2": "Timing",
                    "sub_theme": None, "warnings": []}),
            (v_fail, {"l1": "", "l2": "", "sub_theme": "", "warnings": []})]
    ranges, hdrmap = CA.plan_writeback(header, rows, NOW)
    # first range is the header, at row 1
    assert ranges[0][0].endswith("1:") is False and ranges[0][0].endswith("1")
    assert "pred_l1" in ranges[0][1] and "audited_at" in ranges[0][1]
    # one range per data row after it
    assert len(ranges) == 1 + len(rows)
    # row 1 (the ok one) renders yes/yes and an empty sub_ok, not "None"
    first_row_cells = ranges[1][1]
    assert "yes" in first_row_cells
    assert "None" not in first_row_cells
    # the failed row records why it did not run where a reader will see it
    assert any("timeout" in c for c in ranges[2][1])


def test_a_none_verdict_renders_empty_not_the_word_none():
    cells = CA.cells_for({"l1_ok": None, "l2_ok": None, "sub_ok": None,
                          "miss_bucket": "", "note": ""},
                         {"l1": "", "l2": "", "sub_theme": None,
                          "warnings": []}, NOW)
    assert cells["l1_ok"] == ""
    assert cells["pred_sub_theme"] == ""


def test_defuse_is_applied_to_written_cells():
    """The predicted labels are model text and the reminder note can carry a
    guest's words; a cell opening with '=' executes in Sheets. The plan runs
    every cell through the injected defuser."""
    header = ["review", "l1"]
    v = {"l1_ok": False, "l2_ok": None, "sub_ok": None,
         "miss_bucket": "=DANGER()", "note": ""}
    rows = [(v, {"l1": "=cmd", "l2": "", "sub_theme": "", "warnings": []})]
    ranges, _ = CA.plan_writeback(header, rows, NOW,
                                  defuse=lambda s: "'" + s if s[:1] == "=" else s)
    row_cells = ranges[1][1]
    assert any(c.startswith("'=") for c in row_cells), row_cells
