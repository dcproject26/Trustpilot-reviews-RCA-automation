"""Manual-review classification recovery.

A manually-matched booking came back with L1/L2 = None and a lone sub-theme
("E. Alts related"), which rendered on the card as a half-made classification
with both selects blank — the "sections not loading" symptom. Two fixes:

  1. classify() no longer emits an orphan sub-theme: a sub-theme with no valid
     L1/L2 to anchor it is dropped and said, because the sub-theme registry is
     many-L2-to-one-framework and a sub-theme cannot name its own L1/L2.
  2. recover_l1_l2_from_warehouse() fills a TOTALLY empty classification from
     the booking's own warehouse tag (same taxonomy, keyed on the booking id).
     Claude stays authoritative — the warehouse never overrides a live answer,
     only fills a void.
"""
import asyncio
import json

import pytest

from server.services import classifier as C


def _call(payload):
    async def _fn(prompt):
        return json.dumps(payload)
    return _fn


# ── orphan sub-theme ─────────────────────────────────────────────────────────

def test_an_orphan_sub_theme_is_dropped_when_l1_l2_do_not_validate():
    r = asyncio.run(C.classify("review", {}, [], _call(
        {"l1": None, "l2": None, "sub_theme": "E. Alts related",
         "review_summary": "s", "reasoning": "r"}), "rev"))
    assert r.l1 == "" and r.l2 == ""
    assert r.sub_theme is None, "orphan sub-theme survived with no parent"
    assert any("dropped" in w and "anchor" in w for w in r.warnings), r.warnings
    assert r.is_valid() is False


def test_an_invalid_l2_that_cannot_recover_also_drops_the_sub_theme():
    # l1 invalid and l2 invalid -> both empty -> the sub-theme is an orphan.
    r = asyncio.run(C.classify("review", {}, [], _call(
        {"l1": "Nonsense", "l2": "Also Nonsense", "sub_theme": "A. Whatever",
         "review_summary": "s", "reasoning": "r"}), "rev"))
    assert r.l1 == "" and r.l2 == ""
    assert r.sub_theme is None


def test_a_valid_classification_keeps_its_sub_theme():
    # The orphan drop must NOT fire when L1/L2 are valid — regression guard.
    r = asyncio.run(C.classify("review", {}, [], _call(
        {"l1": "Operations Issue", "l2": "Ticket Issues",
         "sub_theme": "B. Ticket Not Received",
         "review_summary": "s", "reasoning": "r"}), "rev"))
    assert r.l1 == "Operations Issue" and r.l2 == "Ticket Issues"
    assert r.sub_theme == "B. Ticket Not Received"


# ── warehouse recovery ───────────────────────────────────────────────────────

def test_an_empty_classification_is_recovered_from_the_warehouse():
    l1, l2, note = C.recover_l1_l2_from_warehouse(
        "", "", "Operations Issue", "Ticket Issues")
    assert (l1, l2) == ("Operations Issue", "Ticket Issues")
    assert note and "warehouse" in note


def test_the_warehouse_never_overrides_a_live_classification():
    # Claude gave a full pair — untouched, and no note.
    assert C.recover_l1_l2_from_warehouse(
        "Product Issue", "Audio Guide Issues",
        "Operations Issue", "Ticket Issues") == (
        "Product Issue", "Audio Guide Issues", "")


def test_the_warehouse_does_not_override_a_partial_live_answer():
    # Claude gave an L1 but no L2 — still authoritative, not overridden.
    l1, l2, note = C.recover_l1_l2_from_warehouse(
        "Operations Issue", "", "Supply Partner Issue", "Guide No Show")
    assert (l1, l2, note) == ("Operations Issue", "", "")


def test_a_warehouse_pair_that_is_not_valid_taxonomy_is_not_adopted():
    l1, l2, note = C.recover_l1_l2_from_warehouse("", "", "Made Up", "Nope")
    assert (l1, l2, note) == ("", "", "")


def test_an_empty_warehouse_leaves_the_classification_empty():
    assert C.recover_l1_l2_from_warehouse("", "", None, None) == ("", "", "")
    assert C.recover_l1_l2_from_warehouse("", "", "", "") == ("", "", "")


def test_a_warehouse_l1_without_an_l2_is_not_adopted():
    # A half tag is not a classification — do not adopt an L1 with no L2.
    assert C.recover_l1_l2_from_warehouse(
        "", "", "Operations Issue", "") == ("", "", "")
