"""The taxonomy export must not make an unfinished catalogue look finished.

The whole risk in a report like this is the inverse of the usual one: it is
believed. A pair nobody has written a sub-theme framework for and a pair with a
framework containing nothing are different facts, and printing "0" for both
would hand a stakeholder a sheet saying the taxonomy is complete when 21 of 32
pairs have no framework at all.

So the two blanks are distinct strings, every L1/L2 pair appears whether or not
it is mapped, and the coverage count is computed from the code rather than
transcribed from the module's own header — which lists five frameworks as
PENDING and is, as it turns out, well out of date.
"""
import csv
import sys
from types import SimpleNamespace

import pytest

from tools.export_taxonomy import (EMPTY, MISSING, coverage, rows, sop_rows,
                                   _html_doc, main)


class _Tax(SimpleNamespace):
    """A miniature taxonomy module with one mapped pair and one unmapped."""


def _tax():
    return _Tax(
        L1_CATEGORIES=["Ops", "Biz"],
        L1_PRIORITY_ORDER=["Ops", "Biz"],
        OPERATIONS_L2_PRIORITY_ORDER=["Tickets"],
        L2_OPTIONS={"Ops": ["Tickets", "Unmapped L2"], "Biz": ["Pricing"]},
        SUB_THEME_REGISTRY={("Ops", "Tickets"): {
            "l2_key": "Ticket Issues",
            "sub_themes": [["A", "Late delivery", ["late", "delayed"]]],
            "exclusion": ["irrelevant"], "exclusion_label": "G. Irrelevant"}},
        DIAGNOSTIC_CHECKS={"Ops": [{"question": "Sent on time?",
                                    "data_source": "timeline.sent"}]},
        SUPPORT_TAG_MAP={("Ops", "Tickets"): ["Tag One"],
                         ("Ops", "Unmapped L2"): []},
        GAP_TAXONOMY=["Wrong policy applied"],
        ACTION_TABS={"sp": {"label": "Supply Partner", "default_handle": "[sp]"}},
    )


# ── the two blanks ──────────────────────────────────────────────────────────

def test_an_unmapped_pair_still_appears():
    """THE POINT. Dropping it would shrink the catalogue to the finished parts
    and nobody would learn the pair exists."""
    got = rows(_tax())
    pairs = {(r["l1"], r["l2"]) for r in got}
    assert ("Ops", "Unmapped L2") in pairs
    assert ("Biz", "Pricing") in pairs


def test_never_written_and_deliberately_empty_are_different_words():
    """A framework nobody has written and a framework written empty must not
    both print as nothing."""
    assert MISSING != EMPTY
    got = {(r["l1"], r["l2"]): r for r in rows(_tax())}
    assert got[("Ops", "Unmapped L2")]["sub_theme"] == MISSING


def test_a_mapped_pair_with_no_tags_is_not_reported_as_unmapped():
    """SUPPORT_TAG_MAP[('Ops','Unmapped L2')] is [] — somebody wrote it. That
    is not the same as a pair missing from the map."""
    got = {(r["l1"], r["l2"]): r for r in sop_rows(_tax())}
    assert got[("Ops", "Unmapped L2")]["support_tags"] == EMPTY
    assert got[("Biz", "Pricing")]["support_tags"] == MISSING


def test_a_missing_diagnostic_set_is_named_not_blanked():
    got = {(r["l1"], r["l2"]): r for r in sop_rows(_tax())}
    assert got[("Biz", "Pricing")]["diagnostic_checks"] == MISSING
    assert "Sent on time?" in got[("Ops", "Tickets")]["diagnostic_checks"]


# ── content that must survive the export ────────────────────────────────────

def test_sub_themes_carry_their_code_and_keywords():
    got = [r for r in rows(_tax()) if r["sub_code"] == "A"]
    assert len(got) == 1
    assert got[0]["sub_theme"] == "Late delivery"
    assert "late" in got[0]["keywords"] and "delayed" in got[0]["keywords"]


def test_the_exclusion_bucket_is_exported_too():
    """It is how a review gets classified OUT of a framework. Exporting only
    the ways in shows half the rule."""
    got = [r for r in rows(_tax()) if r["sub_theme"] == "G. Irrelevant"]
    assert len(got) == 1 and "irrelevant" in got[0]["keywords"]


def test_coverage_is_computed_not_transcribed():
    have, missing = coverage(_tax())
    assert have == [("Ops", "Tickets")]
    assert ("Ops", "Unmapped L2") in missing and ("Biz", "Pricing") in missing


# ── the real module, and the files ──────────────────────────────────────────

def test_the_real_taxonomy_exports_every_pair():
    import server.taxonomy as t
    got = {(r["l1"], r["l2"]) for r in rows(t)}
    for l1 in t.L1_CATEGORIES:
        for l2 in (t.L2_OPTIONS.get(l1) or []):
            assert (l1, l2) in got, f"{l1} > {l2} missing from the export"


def test_the_html_flags_both_blanks_visibly():
    """On a TABLE CELL, not merely somewhere in the document.

    This asserted `'class="missing"' in doc` and passed against a build where
    the cell class had been stripped entirely — it was matching the legend in
    the note box that EXPLAINS the mark. Mutation testing caught it. Searching a
    whole document for a string the document also documents is not a test of
    the thing being documented.
    """
    tx = _tax()
    have, missing = coverage(tx)
    doc = _html_doc(tx, rows(tx), sop_rows(tx), have, missing, "abc123")
    assert '<td class="missing">' in doc, (
        "not-mapped table cells carry no mark, so an unwritten framework reads "
        "like any other value")
    assert '<td class="empty">' in doc, "deliberately-empty cells carry no mark"
    assert "abc123" in doc, "the export does not stamp the commit it came from"
    assert "Unmapped L2" in doc


def test_the_files_are_written_and_the_csv_is_excel_safe(tmp_path, monkeypatch):
    """utf-8-sig, because Excel reads a plain utf-8 CSV as cp1252 and turns
    every accented venue name into mojibake."""
    monkeypatch.setattr(sys, "argv", ["x", "--out", str(tmp_path)])
    assert main() == 0
    csv_path = tmp_path / "taxonomy_l1_l2_subthemes.csv"
    assert csv_path.exists() and (tmp_path / "taxonomy.html").exists()
    assert csv_path.read_bytes().startswith(b"\xef\xbb\xbf"), "no utf-8 BOM"
    with open(csv_path, encoding="utf-8-sig", newline="") as f:
        got = list(csv.DictReader(f))
    assert got and set(got[0]) == {"l1", "l2", "sub_code", "sub_theme",
                                   "keywords", "framework"}
