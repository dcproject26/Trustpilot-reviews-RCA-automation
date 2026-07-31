"""Structural guarantees for the RCA column that a browser check cannot pin.

Behaviour is verified by driving the real thing — `tools/check_rca_ui.py`
clicks every add, delete and chip-select in Chromium and reports what happened.
These are the assertions that survive being written as text: NEGATIVE ones,
where "this string appears nowhere" cannot be defeated by an unreachable
branch, plus the closed vocabularies, whose whole point is that no other value
can be written.
"""
import re

CLIENT = open("client/index.html", encoding="utf-8").read()


# ── the card-level blocks are gone (§2) ─────────────────────────────────────

def test_the_card_level_analysis_blocks_are_not_rendered_again():
    """Operational failure / SOP gap / Pattern / Fixes rendered four slightly
    different ways, detached from the issue they explained. They live on the
    issue now — a card-level block reappearing means two homes for one field."""
    for gone in ("<span class=\"wwr-blk-label\">Fixes</span>",
                 "<span class=\"wwr-blk-label\">Operational failure</span>",
                 "<span class=\"wwr-blk-label\">SOP gap</span>"):
        assert gone not in CLIENT, f"a card-level block is back: {gone}"


def test_legacy_card_level_content_is_still_rendered_somewhere():
    """Removing the blocks must not delete analysis a previous draft holds.
    It moves under the last issue, at its own v3 path so edits still save.

    The guard is asserted, not just the strings: a first version of this test
    checked only that the paths appeared in the file, and passed against a
    build where the whole block sat behind `false ?`. The rendering itself is
    checked by driving the page — tools/check_rca_ui.py.
    """
    assert "From an earlier draft — not yet attached to an issue" in CLIENT
    assert "what_went_wrong.what_happened.operational_failure" in CLIENT
    assert "what_went_wrong.fixes.actions" in CLIENT
    assert "const legacyExtras = (_legacyRows.length || _spLegacy) ? `" in CLIENT, \
        "the legacy block is behind a guard that can never be true"
    assert "${legacyExtras}" in CLIENT, "it is built but never placed in the section"


# ── closed vocabularies (§1, §9, §11) ───────────────────────────────────────

def test_the_verdict_and_owner_are_selects_not_free_text():
    """A chip that renders whatever the model returns is how a sentence-length
    verdict crushed the issue title to one character per line. A select can
    only render its options."""
    assert "const ACC  = ['Accurate', 'Partly accurate', 'Inaccurate', 'Unknown']" in CLIENT
    assert "const OWNS = ['Content', 'CE', 'SP', 'RO', 'Product', 'Biz', 'Ops']" in CLIENT
    assert "data-v3sel" in CLIENT, "the chip-selects have no save path"


def test_the_raw_verdict_is_kept_in_the_title_not_the_chip():
    i = CLIENT.find("const chipSel =")
    assert 0 < i and 'title="${esc(raw || v)}"' in CLIENT[i:i + 600]


def test_the_accuracy_cap_is_a_cap_and_the_isa_width_is_fixed():
    """Two different kinds of number: accuracy and owner size to content and
    clamp only on garbage; the ISA chip is fixed so five stacked rows share one
    column."""
    assert re.search(r"\.chip-acc-sel\s*\{\s*max-width:\s*140px", CLIENT)
    assert re.search(r"\.chip-owner-sel\s*\{\s*max-width:\s*26ch", CLIENT)
    assert re.search(r"\.chip-isa-sel\s*\{\s*width:\s*82px", CLIENT)


# ── evidence rows (§2b) ─────────────────────────────────────────────────────

def test_evidence_is_a_three_column_grid_with_a_fixed_source_rail():
    assert re.search(r"\.ev-row\s*\{[^}]*grid-template-columns:\s*62px 1fr 16px", CLIENT)


def test_the_source_rail_is_the_only_row_marker():
    """The old row had a leading em-dash AND a source; two markers for one
    row was the defect."""
    i = CLIENT.find("const evRow =")
    assert i > 0
    body = CLIENT[i:i + 1200]
    assert '<span class="dash">' not in body


# ── the claim (defect 2, and the claim-less empty state) ────────────────────

def test_the_claim_editable_is_inline_inside_the_quote():
    """.editable-text is inline-block globally: one atomic box that takes the
    full width the moment it wraps, which put the “ and ” on their own lines.
    Scoped to the claim, it has to be a plain inline."""
    assert ".wwr-claim-q .editable-text { display: inline;" in CLIENT


def test_an_absent_claim_is_never_quote_marks_around_nothing():
    """A blank quote reads as a guest who said "". The neutral empty state
    says what actually happened, and "+ Claim" recreates the field."""
    i = CLIENT.find('<span class="wwr-blk-label">Claim</span>')
    assert i > 0
    body = CLIENT[i:i + 1400]
    assert "does not state this in the guest's own words" in body
    assert "data-claim-add" in body
    assert '“${edSpan(`${bp}.claim`, \'\')}”' not in body, \
        "an empty claim is being rendered as empty quote marks again"


# ── one delete treatment (§2b, §8) ──────────────────────────────────────────

def test_every_delete_uses_the_one_class():
    """Delete controls never vary in colour between rows or sections."""
    assert re.search(r"\.x-del\s*\{[^}]*color:\s*var\(--dim\)", CLIENT)
    assert re.search(r"\.x-del:hover\s*\{\s*color:\s*var\(--red\)", CLIENT)


def test_root_cause_has_no_delete():
    """An issue with no root cause is not an RCA."""
    i = CLIENT.find("Root cause:</span>")
    assert i > 0
    assert "data-aline-del" not in CLIENT[i:i + 400]


# ── parity (§10) ────────────────────────────────────────────────────────────

def test_persistence_never_branches_on_whether_a_row_was_ai_generated():
    """A real bug class: the flag team-select once wrote only to
    operator-added rows, so on AI-generated flags the choice snapped back."""
    for smell in ("isGenerated", "is_generated", "wasAdded", "userAdded",
                  "aiGenerated"):
        assert smell not in CLIENT, f"persistence branches on origin: {smell}"


def test_no_new_colours_were_introduced():
    """Light theme only; every value comes from the :root tokens."""
    i, j = CLIENT.find("/* ── RCA v2: WWR issue internals"), CLIENT.find("</style>")
    block = CLIENT[i:j]
    assert i > 0 and block
    hexes = set(re.findall(r"#[0-9a-fA-F]{3,8}", block))
    assert not hexes, f"new literal colours in the v2 block: {sorted(hexes)}"
