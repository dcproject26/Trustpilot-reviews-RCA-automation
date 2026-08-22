"""A hand-added action row is a dict; a re-run must not flatten it to a string.

THE BUG THIS CLOSES. `patch_action` appends a DICT
({context, with, handle, owner, ...}) to actions_taken, and the sheet export /
card / Slack composer all read those keys. But `hand_typed_actions` and the
`keep`-merge in `actions_from_gaps` did `str(row).strip()` and stored the
result — so on the next RCA edit (which regroups the column) a dict row became
its Python repr, "{'context': 'Refund issued', 'owner': 'FINANCE'}", and every
structured field was lost: the card and CSV then showed an unreadable blob.

Both re-run paths run this regroup, so a person's custom action was corrupted by
any later edit to the RCA. These tests drive the two functions directly and
require the ORIGINAL dict to come out the other side intact.
"""
from server.checklist import hand_typed_actions, actions_from_gaps


def _dict_row():
    return {"context": "Refund issued as a goodwill gesture",
            "with": "the guest", "owner": "FINANCE"}


def test_a_dict_action_row_survives_hand_typed_actions_with_stored_gaps():
    """The attributed path: previous gaps stored, so the row is subtracted
    against them and the remainder kept — as the dict, not its repr."""
    stored = {"finance": [_dict_row()]}
    keep, _ = hand_typed_actions(stored, prev_gaps=[])   # [] = gaps were stored, none matched
    row = (keep.get("finance") or [None])[0]
    assert isinstance(row, dict), f"the dict row was stringified: {row!r}"
    assert row["context"] == "Refund issued as a goodwill gesture"
    assert row["owner"] == "FINANCE"


def test_a_dict_action_row_survives_when_no_prior_gaps_were_stored():
    """The unattributed path (prev_gaps is None): the row is still carried, and
    still as a dict, and it is COUNTED as unattributable rather than silently
    called hand-typed."""
    stored = {"co": [_dict_row()]}
    keep, unattributable = hand_typed_actions(stored, prev_gaps=None)
    row = (keep.get("co") or [None])[0]
    assert isinstance(row, dict), f"the dict row was stringified: {row!r}"
    assert unattributable == 1


def test_the_regroup_end_to_end_keeps_the_dict_structure():
    """The full path a card edit takes: hand_typed_actions → actions_from_gaps
    with keep. The dict must land in the rebuilt column with its keys intact,
    never as '{...}'."""
    stored = {"finance": [_dict_row()]}
    keep, _ = hand_typed_actions(stored, prev_gaps=[])
    tabs, rep = actions_from_gaps([], keep=keep)
    row = (tabs.get("finance") or [None])[0]
    assert isinstance(row, dict), f"the regroup flattened the dict row: {row!r}"
    assert row["owner"] == "FINANCE"
    assert rep["kept"] == 1, "the hand-added row was not carried through the re-run"


def test_a_plain_string_action_row_still_works():
    """Regression: gap-derived string rows are unchanged by the fix."""
    stored = {"co": ["Chat miss — raise with CO"]}
    keep, _ = hand_typed_actions(stored, prev_gaps=[])
    tabs, _ = actions_from_gaps([], keep=keep)
    assert tabs["co"] == ["Chat miss — raise with CO"]


def test_a_dict_row_is_not_duplicated_against_a_matching_gap():
    """The de-dup still fires on a dict row: if a gap already says the same
    thing (by its text), the hand row is not added a second time."""
    stored = {"co": [{"context": "Chat miss — raise with CO"}]}
    keep, _ = hand_typed_actions(stored, prev_gaps=[])
    # a gap that produces the identical text
    gaps = [{"gap": "Chat miss — raise with CO", "team": "co", "source_ref": "ZD-1"}]
    tabs, _ = actions_from_gaps(gaps, keep=keep)
    texts = [(x.get("context") if isinstance(x, dict) else x) for x in tabs["co"]]
    assert texts.count("Chat miss — raise with CO") == 1, texts
