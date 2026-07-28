#!/usr/bin/env python3
"""
Test verify_taxonomy's matching logic against synthetic data.

    python3 tools/test_verify_taxonomy.py

No BigQuery. The point is that verify_taxonomy is itself the thing deciding
whether the tag spec is right, so a bug in it produces a confident wrong
answer - it has already shipped two: a TypeError before the first query, and a
SQL-LIKE-to-regex conversion that reported every pattern dead because re.escape
leaves % alone and the substitution that followed matched nothing.

Exit code is 0 when every case passes.
"""
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tools.verify_taxonomy import report, norm   # noqa: E402

CASES = []


def case(name, fn):
    CASES.append((name, fn))


def _capture(configured, live, is_pattern=False):
    """report() prints; we only want the dead list and no stdout noise."""
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        dead, _empty, _n = report("test", configured, live, is_pattern)
    return dead, buf.getvalue()


# --- LIKE patterns ---------------------------------------------------------
def t_like_matches_substring():
    live = {"Ticket Redemption Details  Content Issue": 12,
            "Delay  Something Else": 3}
    dead, _ = _capture({"content": ["%content%"]}, live, is_pattern=True)
    assert dead == [], f"%content% should match, got dead={dead}"


def t_like_reports_real_misses():
    live = {"Delay  Something Else": 3}
    dead, _ = _capture({"x": ["%content%"]}, live, is_pattern=True)
    assert len(dead) == 1, f"%content% should be dead here, got {dead}"


def t_like_counts_every_match():
    live = {"A content one": 5, "B content two": 7, "unrelated": 99}
    _, out = _capture({"x": ["%content%"]}, live, is_pattern=True)
    assert "12 rows" in out, f"expected 5+7=12 rows, got:\n{out}"


def t_like_anchors_both_ends():
    """'%incorrect details%' must not match a tag lacking the phrase."""
    live = {"incorrect meeting point": 4}
    dead, _ = _capture({"x": ["%incorrect details%"]}, live, is_pattern=True)
    assert len(dead) == 1, "should not match on a partial word overlap"


def t_like_special_chars_are_literal():
    """A '.' in a pattern must not act as a regex wildcard."""
    live = {"a x b": 9}
    dead, _ = _capture({"x": ["%a.b%"]}, live, is_pattern=True)
    assert len(dead) == 1, "'.' must be escaped, not treated as any-char"


# --- exact values ----------------------------------------------------------
def t_exact_matches():
    live = {"Ticket Redemption Details  Sp Information": 8}
    dead, _ = _capture({"x": ["Ticket Redemption Details  Sp Information"]}, live)
    assert dead == [], f"exact value should match, got {dead}"


def t_exact_forgives_double_space():
    """The whole reason norm() exists - a spacing difference is not a miss."""
    live = {"Ticket Redemption Details Sp Information": 8}
    dead, _ = _capture({"x": ["Ticket Redemption Details  Sp Information"]}, live)
    assert dead == [], "double vs single space should still match"


def t_exact_forgives_case():
    live = {"MEETING POINT ISSUES": 2}
    dead, _ = _capture({"x": ["Meeting Point Issues"]}, live)
    assert dead == [], "case should not decide a match"


def t_exact_reports_dead_with_hint():
    live = {"Meeting Point Issue": 5}
    dead, out = _capture({"x": ["Meeting Point Issuez"]}, live)
    assert len(dead) == 1, "a genuinely absent value must be reported"
    assert "closest live value" in out, f"expected a suggestion, got:\n{out}"


def t_exact_does_not_substring_match():
    """'Tickets' must not match 'Invalid Tickets' - that would overcount."""
    live = {"Invalid Tickets": 40}
    dead, _ = _capture({"x": ["Tickets"]}, live)
    assert len(dead) == 1, "exact matching must not be a substring test"


# --- the NAR regex, as insights.py defines it ------------------------------
def t_nar_regex_over_match():
    from server.services import insights as I
    pat = I._NAR_PATTERN
    should_drop = ["Chat Abandoned", "Out Call", "Vendor Query", "NAR",
                   "Auto resolved", "Missed Chat", "Outbound Call"]
    for v in should_drop:
        assert re.search(pat, v, re.I), f"{v!r} should be excluded"
    # The substring risk that made this worth checking.
    assert re.search(pat, "Narrative Issue", re.I), \
        "'Narrative Issue' matches NAR as a substring - documented over-match"


def main():
    for name, fn in CASES:
        try:
            fn()
            print(f"  pass  {name}")
        except AssertionError as e:
            print(f"  FAIL  {name}\n        {e}")
            return 1
    print(f"\n{len(CASES)} cases passed.")
    return 0


for _n, _f in list(globals().items()):
    if _n.startswith("t_") and callable(_f):
        case(_n[2:].replace("_", " "), _f)

if __name__ == "__main__":
    sys.exit(main())
