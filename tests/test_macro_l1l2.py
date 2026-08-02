"""The macros are filed under an L1/L2, and the filing beats word overlap.

Keyword overlap alone matched "Operations Issue / Customer Support Issues" to
the "Audio guide Issue — initiated a partial refund post DOV" macro, because
both contain "refund". Every macro contains refund, booking and sorry; the
words a macro is FILED under are not the words it happens to use. So each
Trustpilot macro carries a hand-assigned L1/L2 and that is consulted first.

The map is a join keyed on a hand-typed string, which is the shape of the
ZD-4491 bug — `"ZD-4491"` against ticket_id `"4491"` matched nothing and read
exactly like a model that returned no notes. A key one character out here
scores no macro, and the run looks identical to one where that macro simply
lost. Hence the coverage tests: the join has to be able to say it ran.
"""
import asyncio

import pytest

import server.services.canned as C
from server.taxonomy import L2_OPTIONS


def test_every_mapping_key_matches_a_real_macro():
    """A key that joins to nothing is not a filing, it is a typo that costs
    nothing visible."""
    cov = C.macro_l1l2_coverage()
    assert cov["keys_matching_no_macro"] == [], (
        "these mapped situations exist in no macro, so they can never score:\n"
        + "\n".join(cov["keys_matching_no_macro"]))


def test_every_trustpilot_macro_is_filed():
    cov = C.macro_l1l2_coverage()
    assert cov["macros_with_no_mapping"] == [], (
        f"{len(cov['macros_with_no_mapping'])} TP macro(s) can only ever win "
        f"on word overlap:\n" + "\n".join(cov["macros_with_no_mapping"]))


def test_coverage_counts_both_directions_separately():
    """An unmapped macro is work not done; a mapped key with no macro is a key
    that is wrong. Summing them into one number hides which."""
    cov = C.macro_l1l2_coverage()
    assert set(cov) >= {"mapped", "macros", "joined",
                        "keys_matching_no_macro", "macros_with_no_mapping"}
    assert cov["joined"] <= min(cov["mapped"], cov["macros"])


def test_every_filed_pair_is_in_the_real_taxonomy():
    """A macro filed under an L2 the classifier can never return is filed under
    nothing at all — it would sit there scoring zero forever."""
    bad = []
    for sit, pair in C.macro_l1l2().items():
        assert isinstance(pair, list) and len(pair) == 2, (sit, pair)
        l1, l2 = pair
        if l1 not in L2_OPTIONS or l2 not in L2_OPTIONS.get(l1, []):
            bad.append(f"{sit} -> {l1} / {l2}")
    assert not bad, "filed under pairs the classifier cannot produce:\n" + \
                    "\n".join(bad)


# ── the filing outranks vocabulary ──────────────────────────────────────────

def _score(situation, l1, l2, review_kw=frozenset()):
    return C._score_row({"situation": situation, "response": "r", "tab": "TP"},
                        l1, l2, "", set(review_kw))


PAIR = ["Product Issue", "Audio Guide Issues"]      # the pair from the bug


def test_the_bug_pair_is_still_in_the_map():
    """The two tests below name a pair. If a re-filing removed it they would
    raise StopIteration inside the fixture and read as an error in the scoring
    code, which is the wrong place to look."""
    assert any(p == PAIR for p in C.macro_l1l2().values()), \
        f"{PAIR} is no longer filed — pick another pair for the tests below"


def test_a_macro_filed_under_the_pair_beats_one_that_shares_words():
    """The actual regression. Both macros say "refund"; only one is filed under
    the review's category, and it has to win by a margin no vocabulary can
    close."""
    filed = next(s for s, p in C.macro_l1l2().items() if p == PAIR)
    on_pair = _score(filed, *PAIR)
    # Every macro filed elsewhere, scored against the same review — not just
    # one, because the one I happened to pick first proves nothing about the
    # other 78.
    off = {s: _score(s, *PAIR)
           for s, p in C.macro_l1l2().items() if p != PAIR}
    worst = max(off.items(), key=lambda kv: kv[1])
    assert on_pair > worst[1], \
        f"{filed}={on_pair} did not beat {worst[0]}={worst[1]}"
    assert on_pair >= C.MATCH_MIN


def test_the_right_l1_with_the_wrong_l2_is_worth_less_than_both():
    filed = next(s for s, p in C.macro_l1l2().items() if p == PAIR)
    other_l2 = next(l2 for l1, l2 in
                    (tuple(p) for p in C.macro_l1l2().values())
                    if l1 == PAIR[0] and l2 != PAIR[1])
    both = _score(filed, *PAIR)
    l1_only = _score(filed, PAIR[0], other_l2)
    neither = _score(filed, "External Factor", "Customer Late")
    assert both > l1_only >= neither, (both, l1_only, neither)


def test_an_unclassified_review_gets_no_filing_bonus():
    """With no L1/L2 there is nothing to file against, and awarding the bonus
    anyway would make every macro look like a category hit."""
    filed = next(iter(C.macro_l1l2()))
    assert _score(filed, "", "") < C._L1L2_EXACT


def test_a_missing_map_falls_back_to_word_overlap_and_says_so(monkeypatch,
                                                              caplog, tmp_path):
    """An unreadable map must not silently become "no macro matched". Word
    overlap still runs; the log carries the reason it is running alone."""
    import logging
    monkeypatch.setattr(C, "MACRO_L1L2", tmp_path / "not_here.json")
    monkeypatch.setattr(C, "_l1l2_map", None, raising=False)
    with caplog.at_level(logging.WARNING, logger="server.services.canned"):
        m = C.macro_l1l2()
    assert m == {}
    assert "word overlap" in " ".join(r.getMessage() for r in caplog.records)
    # and matching still works on words alone
    assert _score("Meeting point issue - venue related",
                  "Experience Issues", "Meeting Point Issues") > 0
    monkeypatch.setattr(C, "_l1l2_map", None, raising=False)


# ── end to end through the real lookup ──────────────────────────────────────

@pytest.mark.parametrize("l1,l2", [
    ("Product Issue", "Audio Guide Issues"),
    ("External Factor", "Customer Late"),
    ("Venue Related Issue", "Venue closure"),
])
def test_the_lookup_returns_the_macro_filed_under_that_pair(l1, l2, monkeypatch):
    """Driven through get_canned_responses, not _score_row, so a scoring change
    that never reaches the caller fails here."""
    monkeypatch.setattr(C, "is_live", lambda s: False)
    monkeypatch.setattr(C, "_cache_rows", [], raising=False)
    monkeypatch.setattr(C, "_cache_at", 0, raising=False)
    got = asyncio.run(C.get_canned_responses(l1, l2, "", "the tour was bad"))
    assert got, f"no macro at all for {l1} / {l2}"
    filed_for_pair = {s for s, p in C.macro_l1l2().items() if p == [l1, l2]}
    assert C._norm_sit(got[0]["situation"]) in filed_for_pair, (
        f"top match {got[0]['situation']!r} is not filed under {l1} / {l2}")
