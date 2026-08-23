"""Worked examples in the classification prompt — the closest thing to training
this system has.

There is no fine-tuning here. The model's weights are fixed and nothing in this
repo can change them, so "train" means: put the right instructions and the right
examples in front of it on every call. Rules cover the boundaries somebody can
articulate. Examples cover the ones nobody can — "guide spoke only English
despite booking another language" and "guide only spoke English and French, not
Spanish as paid" were labelled E and G by the same team, and no rule written
from that would be anything but a guess.

Most of the examples on file are cases the classifier got WRONG against those
labels. An example it already handles teaches it nothing.
"""
import json
import pathlib

import pytest

from server import prompts
from server.taxonomy import has_sub_theme_framework, is_valid_sub_theme

PATH = pathlib.Path("server/data/classification_examples.json")
EX = json.loads(PATH.read_text(encoding="utf-8"))
PROMPT = prompts.classification_prompt("x", {}, [])


# ── the examples reach the model ────────────────────────────────────────────

def test_the_examples_are_in_the_prompt():
    """THE WIRING. A file of examples nothing renders is a file, not training —
    the same shape as a validator wired into no path."""
    assert "WORKED EXAMPLES" in PROMPT
    assert EX, "no examples on file"
    assert EX[0]["review"] in PROMPT
    assert EX[-1]["review"] in PROMPT


def test_every_example_reaches_the_prompt():
    missing = [e["review"] for e in EX if e["review"] not in PROMPT]
    assert not missing, f"{len(missing)} example(s) never rendered: {missing[:3]}"


def test_each_example_carries_its_label_not_just_its_text():
    """A review with no answer next to it is not an example — it is a review.
    The whole point is the pairing, so the LABEL has to render, not only the
    text. Mutation caught this: dropping the `-> sub_theme:` line left every
    review in the prompt and taught the model nothing."""
    block = prompts.classification_examples_block()
    for e in EX:
        st = e.get("sub_theme") or "null"
        assert f"-> sub_theme: {st}" in block, (
            f"example {e['review'][:40]!r} rendered without its label {st!r}")


def test_each_example_is_grouped_under_its_l1_and_l2():
    """The sub_theme alone is ambiguous — an "A." means nothing without the L2
    it sits under. Dropping the `[L1 / L2]` header leaves the model a list of
    bare sub-theme codes. Mutation caught this too."""
    block = prompts.classification_examples_block()
    for e in EX:
        assert f'[{e["l1"]} / {e["l2"]}]' in block, (
            f"no group header for ({e['l1']} / {e['l2']})")


def test_a_missing_file_says_so_rather_than_shrinking_the_prompt(monkeypatch):
    """CLAUDE.md rule 1. A prompt that silently loses its examples looks
    complete and classifies worse, and nothing anywhere names the loss."""
    monkeypatch.setattr(prompts, "_EXAMPLES_PATH", "/nonexistent/examples.json")
    block = prompts.classification_examples_block()
    assert "unavailable" in block
    assert "FileNotFoundError" in block, (
        "the failure does not name itself, so a broken path is indistinguishable "
        "from a file that was legitimately empty")


def test_an_empty_file_reads_differently_from_a_broken_one(monkeypatch, tmp_path):
    p = tmp_path / "empty.json"
    p.write_text("[]", encoding="utf-8")
    monkeypatch.setattr(prompts, "_EXAMPLES_PATH", str(p))
    block = prompts.classification_examples_block()
    assert "none on file" in block and "unavailable" not in block


def test_the_header_count_is_what_was_loaded_not_a_constant(monkeypatch, tmp_path):
    """CLAUDE.md rule 1, in the header line. "55 reviews" hardcoded reads as a
    fact regardless of how many actually loaded, so a truncated or grown file
    would announce a count it does not hold. Driven with a two-example file:
    the header must say two."""
    p = tmp_path / "two.json"
    p.write_text(json.dumps(EX[:2]), encoding="utf-8")
    monkeypatch.setattr(prompts, "_EXAMPLES_PATH", str(p))
    block = prompts.classification_examples_block()
    assert "2 reviews" in block
    assert f"{len(EX)} reviews" not in block, (
        "the header count is fixed to the full set, not the loaded one")


# ── the examples are usable answers ─────────────────────────────────────────

def test_every_example_label_is_valid_in_the_live_taxonomy():
    """An example carrying a label the validator would reject teaches the model
    to produce something that gets dropped — worse than no example."""
    bad = []
    for e in EX:
        st = e.get("sub_theme")
        if st and has_sub_theme_framework(e["l1"], e["l2"]):
            if not is_valid_sub_theme(e["l1"], e["l2"], st):
                bad.append((e["l2"], st))
        elif st and not has_sub_theme_framework(e["l1"], e["l2"]):
            bad.append((e["l2"], f"{st} (no framework for this pair)"))
    assert not bad, bad


def test_the_examples_are_mostly_cases_the_classifier_missed():
    """Where the information is. If this ever inverts, the set has been
    refreshed with easy cases and is teaching the model what it already knows.
    """
    missed = sum(1 for e in EX if e.get("was_a_miss"))
    assert missed > len(EX) / 2, f"only {missed} of {len(EX)} were misses"


def test_the_contested_boundaries_are_covered():
    """The four areas a written rule could not settle. If any drops out, the
    examples have stopped covering the thing they exist for."""
    pairs = {(e["l2"], (e.get("sub_theme") or "")[:1]) for e in EX}
    for l2, codes in (("Ticket Issues", "ABCD"),
                      ("Audio Guide Issues", "ABE")):
        for c in codes:
            assert (l2, c) in pairs, f"no example of {l2} sub-theme {c}"


def test_the_prompt_did_not_balloon():
    """Examples are worth prompt budget; an unbounded set is not. This is a
    tripwire, not a limit — if it fires, prune rather than raise it blindly."""
    assert len(PROMPT) < 70_000, len(PROMPT)


# ── the scorecard's failed clusters are now taught (data-driven fix) ─────────

def test_the_top_confusion_clusters_have_worked_examples():
    """The 500-label scorecard's biggest L2 confusions — Guide Behaviour
    mis-filed as Venue facility, SP Timing vs Guide-quality, Ops/Ticket and
    Meeting Point mix-ups — were fixed by adding the missed reviews as worked
    examples (not by inventing rules). Guard that each cluster is represented,
    so a regression that strips them out fails here rather than silently
    regressing accuracy.
    """
    pairs = {(e["l1"], e["l2"]) for e in EX}
    required = [
        ("Supply Partner Issue", "Guide Behaviour Issues"),
        ("Supply Partner Issue", "Timing Issues"),
        ("Operations Issue", "Ticket Issues"),
        ("Operations Issue", "Meeting Point Issues"),
    ]
    missing = [p for p in required if p not in pairs]
    assert not missing, f"failed-cluster pairs have no worked example: {missing}"
    # and the biggest miss cluster (Guide Behaviour, 9 misses) carries several
    gb = sum(1 for e in EX if (e["l1"], e["l2"]) == ("Supply Partner Issue", "Guide Behaviour Issues"))
    assert gb >= 5, f"Guide Behaviour has only {gb} examples; the miss cluster needs more"
