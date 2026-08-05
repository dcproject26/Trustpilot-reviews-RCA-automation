"""Two vocabularies that had drifted apart, and work that vanished on re-run.

**`fix.owner` was a third team list.** Flags and Actions Taken share the nine
teams because they are joined on them. `fix.owner` kept its own seven —
Content, CE, SP, RO, Product, Biz, Ops — two of which name no chip on the
card at all. A reader is told who owns the fix and then cannot find them
anywhere. Now the same nine, with the same aliases flags use, so a draft
written under the old vocabulary is TRANSLATED rather than failed: it names a
real team and dropping it to null would lose an owner the model got right.

**Actions Taken is recomputed on every run.** A row an associate typed was
therefore gone the next time the RCA was regenerated — the work of deciding it
mattered, discarded with nothing on screen to say so. The previous rows are
now passed in, and anything the guideline corpus does not contain is carried
forward and counted.

The corpus, not the routed subset, is what decides. Against the routed rows
alone, a row the AND deliberately withheld reads as hand-typed and returns
through the back door — at which point the AND has quietly stopped meaning
anything. That version was written first and this file is why it did not ship.
"""
import pytest

from server.checklist import actions_raised, actions_for, _ALL_GUIDELINE_ACTIONS
from server.services.rca_v4_validate import OWNERS, _fix_obj
from server.taxonomy import ACTION_TABS


# ── fix.owner speaks the nine ──────────────────────────────────────────────

def test_the_owner_enum_is_exactly_the_nine_teams():
    assert set(OWNERS) == {t.upper() for t in ACTION_TABS}, (
        f"fix.owner names {sorted(set(OWNERS) - {t.upper() for t in ACTION_TABS})} "
        f"which no Actions Taken tab carries")


@pytest.mark.parametrize("given,expect", [
    ("CE", "CO"), ("RO", "CO"), ("ce", "CO"),
    ("customer", "CO"), ("business", "BIZ"), ("io", "INVENTORY"),
    ("fin", "FINANCE"),
])
def test_a_legacy_owner_is_translated_not_dropped(given, expect):
    """The old vocabulary names a REAL team. Failing it to null loses an owner
    the model identified correctly."""
    notes = []
    got = _fix_obj({"action": "close the gap", "owner": given}, notes)
    assert got["owner"] == expect, f"{given!r} → {got['owner']!r}"
    assert any("fix.owner" in n for n in notes), \
        "the value was rewritten and the trail was never told"


@pytest.mark.parametrize("team", ["SP", "CONTENT", "TECH", "FINANCE", "GUEST"])
def test_a_current_team_passes_through_untouched(team):
    notes = []
    got = _fix_obj({"action": "close the gap", "owner": team}, notes)
    assert got["owner"] == team
    assert not [n for n in notes if "fix.owner" in n], \
        "a value that needed no change was reported as coerced"


def test_a_team_that_is_not_one_of_the_nine_is_nulled_and_said():
    """"Ops" was in the old list and is in no chip. Silently keeping it would
    put an owner on the card that the reader cannot act on."""
    notes = []
    got = _fix_obj({"action": "close the gap", "owner": "Ops"}, notes)
    assert got["owner"] is None
    assert any("not one of the nine" in n for n in notes), notes


def test_the_prompt_asks_for_the_same_nine():
    """A validator that accepts nine and a prompt that asks for seven means
    every draft arrives needing translation. Negative-ish assertion: the two
    retired names must not still be offered."""
    import pathlib
    schema = pathlib.Path("server/prompts.py").read_text()
    i = schema.find('"owner":    "<')
    line = schema[i:i + 200]
    assert "GUEST" in line and "FINANCE" in line, line
    assert "| Ops>" not in line, "the prompt still offers a team with no chip"


# ── a row someone typed survives a re-run ──────────────────────────────────

SCEN = "Redemption issue with tickets"
TYPED = "Call the vendor directly — typed by an associate"


def _real_guideline_row():
    for rows in actions_for([SCEN]).values():
        for r in rows:
            return r
    pytest.skip("the guidelines produce no action for this scenario")


def test_a_typed_row_is_carried_through_a_rerun():
    prev = {"sp": [TYPED]}
    tabs, rep = actions_raised([SCEN], [{"team": "SP"}], keep=prev)
    assert TYPED in tabs["sp"], (
        "a row an associate typed was discarded by the re-run, with nothing "
        "on screen to say it had been")
    assert rep["kept_by_hand"] == 1


def test_the_carry_forward_is_announced():
    """These rows are on the card for a different reason from the rest, and a
    reader comparing two runs deserves to know which survived because a person
    wrote them."""
    tabs, rep = actions_raised([SCEN], [{"team": "SP"}], keep={"sp": [TYPED]})
    assert any("hand-added" in n for n in rep["notes"]), rep["notes"]


def test_a_guideline_row_is_not_smuggled_back_as_hand_written():
    """The whole point. A row the AND withheld must not return by this door —
    it is the sheet's, not a person's, and resurrecting it makes the AND
    meaningless."""
    row = _real_guideline_row()
    tabs, rep = actions_raised([SCEN], [], keep={"sp": [row], "tech": [row]})
    assert rep["kept_by_hand"] == 0, (
        f"{row!r} came from the guidelines and was carried forward as though "
        f"a person had typed it")
    assert row not in tabs["sp"]


def test_the_corpus_is_what_decides_not_the_routed_subset():
    """A row from ANOTHER scenario's guidelines is still the sheet's. Judged
    against the routed rows alone it would read as hand-typed."""
    other = next(a for a in _ALL_GUIDELINE_ACTIONS
                 if a not in set().union(*[set(v) for v in actions_for([SCEN]).values()] or [set()]))
    tabs, rep = actions_raised([SCEN], [{"team": "SP"}], keep={"sp": [other]})
    assert rep["kept_by_hand"] == 0, f"{other!r} is a guideline row, not hand-typed"


def test_no_previous_rows_means_nothing_kept_and_nothing_said():
    """The ordinary first run. A count of zero must not produce a note — a
    trail line on every clean run is a trail nobody reads."""
    tabs, rep = actions_raised([SCEN], [{"team": "SP"}], keep=None)
    assert rep["kept_by_hand"] == 0
    assert not [n for n in rep["notes"] if "hand-added" in n]


def test_a_typed_row_is_not_duplicated_when_it_matches_a_raised_one():
    """If the guidelines later add the same sentence, carrying it forward as
    well would show it twice."""
    row = _real_guideline_row()
    team = next(t for t, rows in actions_for([SCEN]).items() if row in rows)
    tabs, _ = actions_raised([SCEN], [{"team": team.upper()}], keep={team: [row]})
    assert tabs[team].count(row) == 1, tabs[team]
