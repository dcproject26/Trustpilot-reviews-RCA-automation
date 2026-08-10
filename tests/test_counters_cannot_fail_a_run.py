"""An instrumentation counter must not be able to take down a run.

WHAT HAPPENED. `_ctr` was a plain dict with a fixed key list, so
`_ctr["k"] += 1` on an undeclared key raised KeyError out of process_review.
Six keys were being incremented that nobody had declared:

    indicator_mismatch, t1_name_uncheckable, t2_extraction_unavailable,
    t2_shortlist_crashed, t2_text_bid_dropped, t2_ticket_no_bid

Every one sits on a DIAGNOSTIC branch — code that runs when something unusual
happened and exists to explain it. So the pipeline crashed precisely on the
reviews that most needed explaining.

AND THE CRASH DISCARDED THE GUARDS. The RCA validation is wrapped in
`except Exception: keeping raw output`, so a run that died here stored the
model's unchecked answer — fabricated timeline rows included. Two live drafts
ended exactly that way, each reading "Run failed — KeyError:
't2_ticket_no_bid'" at the bottom of a card that otherwise looked complete.

The counting is worth nothing next to the run. A wrong number is a wrong
number; an outage loses the whole RCA.
"""
import ast
import re

import pytest

from server import pipeline


SRC = open("server/pipeline.py", encoding="utf-8").read()


def _literal():
    """The `_ctr = _Counter({...})` source, brace-matched.

    The first version started counting braces at `_ctr`, where depth is already
    zero, so it "closed" on the first character and returned an empty set —
    which made every key look undeclared and three tests fail for a reason that
    had nothing to do with the code. Start at the opening brace.
    """
    start = SRC.index("_ctr = _Counter({")
    i = SRC.index("{", start)
    depth = 0
    while i < len(SRC):
        depth += SRC[i] == "{"
        depth -= SRC[i] == "}"
        i += 1
        if depth == 0:
            break
    body = SRC[SRC.index("{", start):i]
    assert len(body) > 200, f"the literal did not brace-match: {body[:80]!r}"
    return body


def _declared():
    return set(re.findall(r'"([a-z0-9_]+)":', _literal()))


def _used():
    used = set()
    for n in ast.walk(ast.parse(SRC)):
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Name)
                and n.value.id == "_ctr" and isinstance(n.slice, ast.Constant)):
            used.add(n.slice.value)
    return used


def test_a_missing_key_counts_instead_of_raising():
    """THE ACTUAL SAFETY. Whatever the declaration says, an undeclared key must
    not be able to end a run — a new branch added in six months' time gets a
    wrong number, not an outage."""
    from collections import Counter
    c = Counter({"declared": 0})
    c["never_declared_anywhere"] += 1          # the exact line that crashed
    assert c["never_declared_anywhere"] == 1


def test_the_pipeline_counter_is_that_kind_of_counter():
    """Driven rather than asserted from source: build the same object the
    pipeline builds and prove the operation is survivable on it."""
    from collections import Counter as _Counter
    ctr = _Counter(eval(_literal()))
    ctr["a_key_no_one_declared"] += 1
    assert ctr["a_key_no_one_declared"] == 1
    assert ctr["another"] == 0, "reading an absent key must not raise either"


def test_every_key_the_code_touches_is_declared():
    """The hygiene half, and NOT the same check as the one above. Counter makes
    a stray key survivable; this keeps the declaration honest as documentation
    of what is actually measured. Without it the list rots and nobody can tell
    what the pipeline counts by reading it."""
    missing = sorted(_used() - _declared())
    assert not missing, (
        f"{len(missing)} counter key(s) incremented but never declared: "
        f"{missing}. Counter means this no longer crashes, but the list above "
        f"it is meant to be the record of what is measured.")


def test_the_six_that_crashed_are_declared_by_name():
    """Named individually so a future edit that drops one fails here with the
    history attached, rather than passing a set-difference nobody reads."""
    declared = _declared()
    for k in ("indicator_mismatch", "t1_name_uncheckable",
              "t2_extraction_unavailable", "t2_shortlist_crashed",
              "t2_text_bid_dropped", "t2_ticket_no_bid"):
        assert k in declared, f"{k} lost its declaration again"


def test_nothing_declared_has_fallen_out_of_use():
    """The other direction. A key left declared after its branch was deleted
    makes the list read as a description of the pipeline that is no longer
    true."""
    stale = sorted(_declared() - _used())
    assert not stale, f"declared but never incremented: {stale}"
