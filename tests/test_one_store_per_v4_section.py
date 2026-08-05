"""One fact, one store. The card and the payload must not answer differently.

THE BUG. The takedown verdict chip renders from `rca.v3.takedown` — the
client's only copy, lifted straight out of the `rca_v3` blob `_draft_dict`
ships. Everything else reads the top-level `takedown`, which `_v4()` resolves
by preferring rca_v3 and falling back to the `takedown` COLUMN. Populate the
column and leave rca_v3 alone and the two disagree: the payload, the Slack
post and the sheet export say "Yes", the chip on the card says "No". A test
that set the column got a chip showing the other store.

It was never only takedown — all six v4 sections have that exact shape. This
is the same family as the four copies of the team vocabulary behind the owner
bug, so the test is written over the WHOLE map rather than over the one field
that was noticed. A seventh section added later is covered the day it is added.

Everything here drives `_draft_dict` on a real draft object. There is no
assertion that a string appears in a source file.
"""
import pytest

from server.api import _draft_dict, _resolve_v3_sections, _V4_SECTIONS
from server.db import RcaDraft

# A distinctive value per section, shaped like the real thing so a renderer
# reading it does not blow up on the type.
COLUMN_VALUES = {
    "takedown":          {"verdict": "Yes", "reason": "Factually incorrect"},
    "flags":             [{"team": "TECH", "flag": "from the column"}],
    "booking_logs":      [{"time": "10:00", "what": "from the column"}],
    "guest_issues":      [{"issue": "from the column"}],
    "dss":               {"guideline": "from the column"},
    "area_of_improving": [{"point": "from the column", "from": "flag",
                           "source": "x"}],
}


def _dig(blob, path):
    """The value at a dotted/tuple path, or KeyError-free sentinel."""
    node = blob
    for part in path:
        if not isinstance(node, dict) or part not in node:
            return _MISSING
        node = node[part]
    return node


_MISSING = object()


def test_the_map_was_found_at_all():
    """A parametrised suite that enumerated nothing would pass in silence.

    That is the failure mode this codebase punishes hardest, and a
    section-by-section test is exactly where it hides.
    """
    assert len(_V4_SECTIONS) >= 6, (
        f"_V4_SECTIONS holds {len(_V4_SECTIONS)} sections — the import found "
        f"the wrong object, and every case below is vacuous")
    missing = set(_V4_SECTIONS) - set(COLUMN_VALUES)
    assert not missing, (
        f"{sorted(missing)} were added to _V4_SECTIONS with no sample value "
        f"here, so they are NOT covered by the divergence check below. Add "
        f"one — that is the whole point of enumerating the map.")


@pytest.mark.parametrize("column", sorted(COLUMN_VALUES))
def test_column_only_reaches_the_blob_the_card_renders(column):
    """Set the column, leave rca_v3 empty: both stores must say the same.

    Before the fix the top-level field carried the column's value and the
    `rca_v3` blob — which is the only thing the card's chip reads — did not.
    """
    d = RcaDraft(id="d1", review_id="tp_1", rca_v3={},
                 **{column: COLUMN_VALUES[column]})
    out = _draft_dict(d)

    path = _V4_SECTIONS[column]
    in_blob = _dig(out["rca_v3"], path)
    assert in_blob is not _MISSING, (
        f"{column}: the column is populated and the payload's top-level field "
        f"reports it, but rca_v3.{'.'.join(path)} is absent — the card renders "
        f"from the blob, so it shows the empty state while everything else "
        f"shows the value")
    assert in_blob == COLUMN_VALUES[column]
    assert out[column] == in_blob, (
        f"{column}: the top-level field and the blob disagree "
        f"({out[column]!r} vs {in_blob!r}) — two stores, one fact")
    assert column in out["v4_sections_from_column"], (
        f"{column} was folded in from the column and the payload does not say "
        f"so — a resolver that ran silently cannot be told from one that did "
        f"not run")


@pytest.mark.parametrize("column", sorted(COLUMN_VALUES))
def test_rca_v3_still_wins_over_a_populated_column(column):
    """The column must not shadow an edit. This is the inverse bug.

    Folding on truthiness rather than presence would reintroduce it.
    """
    edited = {"verdict": "No"} if isinstance(COLUMN_VALUES[column], dict) \
        else [{"flag": "edited on the card", "team": "BIZ", "point": "edited",
               "what": "edited", "issue": "edited", "from": "flag",
               "source": "y"}]
    path = _V4_SECTIONS[column]
    v3 = {}
    node = v3
    for part in path[:-1]:
        node[part] = {}
        node = node[part]
    node[path[-1]] = edited

    d = RcaDraft(id="d1", review_id="tp_1", rca_v3=v3,
                 **{column: COLUMN_VALUES[column]})
    out = _draft_dict(d)

    assert _dig(out["rca_v3"], path) == edited, (
        f"{column}: the column shadowed the edited rca_v3 value")
    assert out[column] == edited
    assert column not in out["v4_sections_from_column"], (
        f"{column} was reported as folded in from the column, but rca_v3 had "
        f"its own answer")


@pytest.mark.parametrize("column", sorted(COLUMN_VALUES))
def test_a_deliberately_emptied_section_beats_a_populated_column(column):
    """Delete the last flag and the dashboard sends `[]`.

    Falling back on falsiness would let the column win, so the delete would
    appear to work and undo itself on the next load — which is the exact bug
    `_v4`'s presence rule exists for. The fold must obey the same rule.
    """
    empty = {} if isinstance(COLUMN_VALUES[column], dict) else []
    path = _V4_SECTIONS[column]
    v3 = {}
    node = v3
    for part in path[:-1]:
        node[part] = {}
        node = node[part]
    node[path[-1]] = empty

    d = RcaDraft(id="d1", review_id="tp_1", rca_v3=v3,
                 **{column: COLUMN_VALUES[column]})
    out = _draft_dict(d)

    assert _dig(out["rca_v3"], path) == empty, (
        f"{column}: an emptied section was refilled from the column — the "
        f"delete undoes itself on the next load")
    assert out[column] == empty


def test_neither_store_has_it_and_nothing_is_invented():
    """No column, no rca_v3: the fold must add nothing and say it added nothing.

    An empty account here is the healthy case, and it has to be reported as
    an empty list rather than as an absent key.
    """
    d = RcaDraft(id="d1", review_id="tp_1", rca_v3={})
    out = _draft_dict(d)
    assert out["v4_sections_from_column"] == [], (
        "sections were folded in from columns that hold nothing")
    assert "v4_sections_from_column" in out, (
        "the account is missing entirely, which reads as 'the resolver never "
        "ran' — the thing it must never look like")


def test_a_nested_section_builds_its_parent():
    """guest_issues lives at rca_v3.what_went_wrong.guest_issues.

    A draft whose rca_v3 has no `what_went_wrong` at all must still receive
    it, or the one nested section silently never folds.
    """
    d = RcaDraft(id="d1", review_id="tp_1", rca_v3={},
                 guest_issues=[{"issue": "nested"}])
    out = _draft_dict(d)
    assert out["rca_v3"]["what_went_wrong"]["guest_issues"] == [{"issue": "nested"}]


def test_a_nested_sibling_is_not_clobbered():
    """Building the parent must not discard what was already in it."""
    d = RcaDraft(id="d1", review_id="tp_1",
                 rca_v3={"what_went_wrong": {"summary": "keep me"}},
                 guest_issues=[{"issue": "nested"}])
    out = _draft_dict(d)
    wwr = out["rca_v3"]["what_went_wrong"]
    assert wwr["summary"] == "keep me", "folding the section destroyed a sibling"
    assert wwr["guest_issues"] == [{"issue": "nested"}]


def test_the_stored_row_is_not_mutated():
    """The fold is a read-path repair. It must not write to the draft.

    Mutating d.rca_v3 here would make a GET silently persist the column's
    value into the source of truth on the next commit — a store changed by
    somebody reading it.
    """
    stored = {"takedown": {"verdict": "No"}}
    d = RcaDraft(id="d1", review_id="tp_1", rca_v3=stored,
                 flags=[{"team": "TECH", "flag": "col"}])
    out = _draft_dict(d)
    assert out["rca_v3"]["flags"] == [{"team": "TECH", "flag": "col"}]
    assert "flags" not in stored, "reading the draft mutated the stored rca_v3"
    assert d.rca_v3 is stored


def test_the_resolver_is_driveable_on_its_own():
    """_resolve_v3_sections returns the blob AND the account, so a caller can
    report what it could not do rather than guessing."""
    d = RcaDraft(id="d1", review_id="tp_1", rca_v3={"flags": []},
                 flags=[{"team": "TECH", "flag": "col"}],
                 takedown={"verdict": "Yes"})
    blob, folded = _resolve_v3_sections(d)
    assert blob["flags"] == [], "the emptied list lost to the column"
    assert blob["takedown"] == {"verdict": "Yes"}
    assert folded == ["takedown"], (
        f"the account is wrong: {folded!r} — flags came from rca_v3 and only "
        f"takedown was folded")
