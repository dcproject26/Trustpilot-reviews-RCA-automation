"""Not asked is not "nothing found", and the first run is always not asked.

WHAT WAS WRONG. The Zendesk fetch is guarded by `if bid_for_zd:` — every
ticket search is keyed on a booking id — and there was no `else`. A review
whose booking is not matched yet therefore skipped Zendesk entirely, and
skipped it in exactly the way a booking with no tickets looks:

    empty events timeline, empty Guest <-> Support panel, and a trail that
    says nothing at all

The other Zendesk trail entries hang off `search_tally`, which only exists
once a search has run, so they were silent too. That made this the most
common state on the card — the first run of nearly every review — and the
least visible one.

And it compounds. An empty record is what tips the RCA prompt into narrating
the guest's review instead of listing events, so the card looked FULLEST
exactly when nobody had been asked anything. One live case: seven Zendesk
tickets existed, the first run never looked for them, and the timeline filled
with four rows restating the review.
"""
import inspect

import pytest

from server import pipeline
from server.pipeline import not_searched_entry as entry


BOTH = ["zendesk", "slack"]


@pytest.mark.parametrize("which", BOTH)
def test_it_says_no_search_ran_rather_than_nothing_was_found(which):
    """The distinction the whole line exists for. "No contact found" and "we
    did not look" call for opposite next actions."""
    t = entry(which)["text"]
    assert "not searched" in t.lower()
    assert "no search ran" in t.lower()


def test_each_rules_out_ITS_OWN_wrong_conclusion():
    """Naming the cause is not enough if the reader's default reading survives
    it — and the two defaults are different. An empty Zendesk reads as a guest
    who never wrote in; empty Slack mentions read as a booking nobody
    discussed. One shared sentence would rule out the wrong one half the time,
    which is why the wording is per-lookup rather than generic."""
    assert "never wrote in" in entry("zendesk")["text"]
    assert "nobody discussed internally" in entry("slack")["text"]
    assert entry("zendesk")["text"] != entry("slack")["text"]


@pytest.mark.parametrize("which", BOTH)
def test_it_names_what_would_make_the_search_run(which):
    """An error that does not say what would work leaves the reader where they
    started — CLAUDE.md's rule, and here the action is not obvious: the fix is
    to confirm a booking, which is a different part of the card entirely."""
    t = entry(which)["text"]
    assert "booking" in t.lower()
    assert "re-run" in t.lower()


@pytest.mark.parametrize("which", BOTH)
def test_it_is_a_warning_not_a_pass(which):
    """A step that did not run is not a step that succeeded. `pass` would file
    it beside the lookups that worked."""
    assert entry(which)["mark"] == "warn"


@pytest.mark.parametrize("which", BOTH)
def test_each_names_its_own_lookup(which):
    """A line that says "Zendesk" under the Slack skip sends the reader to the
    wrong system."""
    assert entry(which)["text"].lower().startswith(
        f"<strong>{which}"), entry(which)["text"][:60]


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_pipeline_calls_it():
    """NEGATIVE source assertion, permitted by CLAUDE.md, and paired with the
    positive one below so neither stands alone. A second inline copy of this
    sentence would drift, and the copy that renders would be the untested one.
    """
    src = inspect.getsource(pipeline.process_review)
    assert 'not_searched_entry("zendesk")' in src, "the Zendesk call is gone"
    assert 'not_searched_entry("slack")' in src, "the Slack call is gone"
    assert "was not searched" not in src, \
        "the pipeline has grown its own copy of the sentence"


@pytest.mark.parametrize("which,marker", [("zendesk", "get_timeline"),
                                          ("slack", "search_mentions")])
def test_the_branch_it_lives_in_is_the_no_booking_id_one(which, marker):
    """WHERE it is called, not just that it is. Appended unconditionally it
    would fire on every review including ones whose search ran fine, and the
    trail would tell every reader the opposite of the truth.

    Read off the source with ast rather than by eye: the guard is what decides
    whether this is a report or a lie, and `if bid_for_zd:` having an `else`
    at all is the entire fix.
    """
    import ast
    tree = ast.parse(inspect.getsource(pipeline.process_review))
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        if "bid_for_zd" not in ast.dump(node.test):
            continue
        # TWO guards read bid_for_zd — the Zendesk timeline and the Slack
        # mention search. Only the first is this line's business, so it is
        # identified by what its body DOES rather than by position: a test
        # keyed on "the first one" would silently start checking the other if
        # the blocks were ever reordered.
        body_src = "".join(ast.dump(n) for n in node.body)
        if marker not in body_src:
            continue
        assert node.orelse, "`if bid_for_zd:` has no else — the skip is silent again"
        else_src = "".join(ast.dump(n) for n in node.orelse)
        assert f'not_searched_entry' in else_src and which in else_src, \
            f"the else exists but does not report the {which} skip"
        # And the converse: it must NOT be in the branch where the search ran.
        assert "not_searched_entry" not in body_src, \
            "it also fires when the search DID run"
        found = True
    assert found, f"no `if bid_for_zd:` guarding {marker} — the guard moved"
