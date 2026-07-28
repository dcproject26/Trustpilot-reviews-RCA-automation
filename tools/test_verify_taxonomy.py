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
    # Every value confirmed live in the warehouse over 180 days.
    should_drop = ["Chat Abandoned", "Nar", "Vendor Query", "Out Call",
                   "Missed Chat Messaging", "Blank Call/no Response",
                   "Vendor Ticket Email"]
    for v in should_drop:
        assert re.search(pat, v, re.I), f"{v!r} should be excluded"

    # "Auto resolved" is deliberately NOT in the pattern. Zero rows carry it as
    # a query_tag while 34,241 have is_auto_resolved = TRUE, so it is excluded
    # on the column instead. If it creeps back into the regex, the column
    # predicate is what is doing the work and the regex term is dead weight.
    assert not re.search(pat, "Auto resolved", re.I), \
        "Auto resolved is handled by is_auto_resolved, not by the pattern"


def t_auto_resolved_excluded_on_the_column():
    from server.services import insights as I
    import inspect
    src = inspect.getsource(I.get_insights)
    assert "NOT sq.is_auto_resolved" in src, \
        "the support filter must exclude auto-resolved contacts on the column"


# --- l2_variants: the classifier's L2 -> the warehouse's spellings ---------
def t_l2_slash_spacing_is_ignored_on_lookup():
    """
    The classifier emits "Content - Instructions not clear / Misleading Info";
    the Looker mapping and the warehouse both write it without spaces around
    the slash. Compared literally it missed its bucket and reported zero
    similar reviews for every booking.
    """
    from server.services.insights import l2_variants
    vs = l2_variants("Content - Instructions not clear / Misleading Info")
    assert "content - instructions not clear/misleading info" in vs, \
        f"the live spelling must be searched, got {vs}"


def t_l2_always_matches_its_own_name():
    from server.taxonomy import L2_OPTIONS
    from server.services.insights import l2_variants
    for l2s in L2_OPTIONS.values():
        for l2 in l2s:
            vs = l2_variants(l2)
            own = re.sub(r"\s+", " ", l2).strip().lower()
            assert own in vs, f"{l2!r} cannot match itself: {vs}"


def t_l2_variants_are_raw_spellings():
    """
    Variants are compared against the warehouse column, so they must look like
    what is stored. Only the LOOKUP is slash-insensitive - if the returned
    values were normalised too, they would stop matching the column.
    """
    from server.services.insights import l2_variants
    vs = l2_variants("Guide Behaviour Issues")
    assert "guide providing irrelevant/inexperienced/not clear" in vs, \
        f"raw slash spelling must survive, got {vs}"


def t_l2_buckets_are_not_pruned():
    """Dead buckets are kept deliberately - they cost nothing and the
    vocabulary may change. This pins the count so none go missing."""
    from server.services.insights import _L2_BUCKETS
    assert len(_L2_BUCKETS) == 21, f"expected 21 buckets, got {len(_L2_BUCKETS)}"
    assert sum(len(v) for v in _L2_BUCKETS.values()) == 80, "bucket values changed"


# --- the two normalisers must stay in step --------------------------------
def t_norm_collapses_and_folds():
    from server.services.insights import _norm
    assert _norm("  Ticket Redemption Details  Sp Information ") == \
        "ticket redemption details sp information"
    assert _norm("A\tB\nC") == "a b c"
    assert _norm(None) == ""


def t_norm_sql_mirrors_norm():
    """
    The SQL twin must lowercase, collapse whitespace and trim - the same three
    operations, in a form BigQuery applies to the column. If they drift, the
    probe validates one comparison and production runs another.
    """
    from server.services.insights import _norm_sql
    expr = _norm_sql("x")
    assert "LOWER(x)" in expr, expr
    assert "REGEXP_REPLACE" in expr and r"\s+" in expr, expr
    assert expr.startswith("TRIM("), expr


def t_taxonomy_comparisons_are_normalised():
    """
    Every taxonomy comparison in the built SQL goes through _norm_sql. A bare
    LOWER() or a raw column here is the bug this closes: tag values carry
    double spaces, so a literal comparison misses them.
    """
    import asyncio
    from server.services import insights as I
    seen = []

    async def fake(sql, params):
        seen.append(sql)
        return []

    r, m, l = I._run, I.MOCK_MODE, I.is_live
    I._run, I.MOCK_MODE, I.is_live = fake, False, (lambda *a, **k: True)
    try:
        asyncio.run(I.get_insights(
            {"tid": "1", "vid": "2", "tgid": "3", "visitDate": "2026-06-01"},
            "Operations Issue", "Meeting Point Issues", window="30d"))
    finally:
        I._run, I.MOCK_MODE, I.is_live = r, m, l

    for sql in seen:
        for line in sql.splitlines():
            if "UNNEST(@l2v)" in line or "UNNEST(@tags)" in line or "LIKE @pat" in line:
                assert "REGEXP_REPLACE" in line, f"unnormalised comparison: {line.strip()}"


def t_guide_l2s_do_not_collapse():
    """
    Looker's bucket lumps every guide complaint together, which made
    "Guide No Show" report 1,714 similar reviews when three reviews are
    guide-no-show. On an RCA the question is whether THIS failure recurs.
    """
    from server.services.insights import l2_variants
    a = set(l2_variants("Guide No Show"))
    b = set(l2_variants("Guide Behaviour Issues"))
    c = set(l2_variants("Guide providing irrelevant/inexperienced/not clear"))
    assert a != b and b != c and a != c, "guide L2s must resolve distinctly"
    assert "guide no show" in a and len(a) <= 2, f"Guide No Show too broad: {a}"


def t_alias_wins_over_bucket():
    """An L2 with explicit aliases must not also drag in its coarse bucket."""
    from server.services.insights import l2_variants, _L2_BUCKETS
    vs = set(l2_variants("Ticket Issues"))
    bucket = {v.lower() for v in _L2_BUCKETS["Ticket Delivery Issues(FF issues)"]}
    assert not (bucket - vs) or "lost tickets" not in vs, \
        f"bucket leaked into an aliased L2: {vs}"


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
