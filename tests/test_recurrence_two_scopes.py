"""Recurrence at both scopes, with the booking ids to go and look.

"TID+VID answers 'is this vendor's version of this experience failing',
TGID answers 'is this experience failing'. Both are legitimate; they're
different questions." — okay show both.

A tour that is fine on three vendors and broken on the fourth reads HEALTHY at
TGID and BROKEN at TID+VID. The two route to different teams, so one number
cannot stand in for the other, and picking either one silently answers a
question the reader did not ask.

Driven through `get_insights` against a stubbed warehouse. The queries
themselves cannot be run here, so what is checked is what the panel receives:
that both scopes arrive, that they are computed from DIFFERENT queries rather
than one aliased twice, and that ids come back beside the counts.
"""
import asyncio

import pytest

import server.services.insights as ins


BOOKING = {"id": "32908218", "tid": "43605", "vid": "4040", "tgid": "22238",
           "experience_date": "2026-07-24"}


def _drive(booking=None, l1="Operations Issue", l2="Ticket Issues",
           row=None, window="30d"):
    """get_insights with every query stubbed. Returns (payload, sql_run)."""
    calls = []

    async def fake_run(sql, params):
        calls.append(sql)
        return [dict(row or {"c": 7, "ids": ["111", "222"], "done": 8,
                             "total": 10, "avg_rating": 4.2, "n_ratings": 9})]

    real_run, real_live = ins._run, ins.is_live
    ins._run = fake_run
    ins.is_live = lambda *a, **k: True
    try:
        out = asyncio.run(ins.get_insights(
            booking=BOOKING if booking is None else booking,
            l1=l1, l2=l2, window=window))
    finally:
        ins._run, ins.is_live = real_run, real_live
    return out, calls


# ── the invariant that makes every other number trustworthy ────────────────

def test_the_result_names_line_up_with_the_queries():
    """_RESULT_NAMES is matched to the gathered coroutines by POSITION and by
    nothing else. A query added without its name shifts every label after it,
    and the panel reports a dozen numbers under the wrong headings — each one
    individually plausible, which is what makes it dangerous.

    Read off the source rather than from a run, because the mismatch has to be
    caught even when every query happens to be skipped.
    """
    import ast, pathlib
    tree = ast.parse(pathlib.Path("server/services/insights.py").read_text())

    names = gathers = None
    for node in ast.walk(tree):
        if (isinstance(node, ast.Assign) and node.targets
                and getattr(node.targets[0], "id", "") == "_RESULT_NAMES"):
            names = len(node.value.elts)
        if (isinstance(node, ast.Call)
                and getattr(getattr(node.func, "attr", None), "__str__", str)() == "gather"):
            gathers = len(node.args)

    assert names, "could not find _RESULT_NAMES — the parse missed it"
    assert gathers, "could not find the asyncio.gather call"
    assert names == gathers, (
        f"{gathers} queries and {names} names: every failure from index "
        f"{min(names, gathers)} on is reported under the wrong query")


# ── both scopes arrive ─────────────────────────────────────────────────────

def test_both_scopes_are_returned():
    out, _ = _drive()
    rec = out["recurrence"]
    assert set(rec) == {"tidvid", "tgid"}


@pytest.mark.parametrize("scope", ["tidvid", "tgid"])
@pytest.mark.parametrize("key", ["reviews", "reviews_total", "review_ids",
                                 "support", "support_total", "support_ids",
                                 "scope"])
def test_each_scope_carries_every_field(scope, key):
    out, _ = _drive()
    assert key in out["recurrence"][scope], f"{scope}.{key}"


def test_each_scope_says_which_scope_it_is():
    """Two numbers side by side with no labels is worse than one number."""
    out, _ = _drive()
    assert out["recurrence"]["tidvid"]["scope"] == "TID + VID"
    assert out["recurrence"]["tgid"]["scope"] == "TGID"


def test_the_two_scopes_are_computed_from_different_queries():
    """The failure this most plausibly degrades into: one query aliased under
    two names, so the panel shows the same number twice under two headings and
    nothing looks wrong."""
    _, calls = _drive()
    tidvid = [s for s in calls if "b.tour_id = @tid" in s]
    tgid   = [s for s in calls if "experience_id = @tgid" in s]
    assert tidvid, "no TID+VID-scoped query ran"
    assert tgid, "no TGID-scoped query ran"
    assert not (set(tidvid) & set(tgid)), \
        "a query is being counted as both scopes"


def test_the_tgid_scope_does_not_filter_on_the_vendor():
    """If it did it would be the TID+VID number under a TGID label — which is
    a defect this file's own comments record happening once already, for the
    rating tiles."""
    _, calls = _drive()
    for sql in [s for s in calls if "experience_id = @tgid" in s]:
        assert "vendor_id = @vid" not in sql, sql[:300]


# ── booking ids ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("scope", ["tidvid", "tgid"])
@pytest.mark.parametrize("key", ["review_ids", "support_ids"])
def test_the_ids_come_back_beside_the_counts(scope, key):
    """"3 reviews" is a number; three booking ids are something an associate
    can open."""
    out, _ = _drive()
    assert isinstance(out["recurrence"][scope][key], list)


def test_the_review_ids_are_actually_populated():
    out, _ = _drive()
    assert out["recurrence"]["tidvid"]["review_ids"] == ["111", "222"]
    assert out["recurrence"]["tgid"]["review_ids"] == ["111", "222"]


def test_the_queries_ask_for_the_ids():
    _, calls = _drive()
    counting = [s for s in calls if "COUNT(DISTINCT" in s and "@nar" not in s]
    assert counting, "no counting query ran at all"
    with_ids = [s for s in counting if "ARRAY_AGG" in s]
    assert with_ids, "no count query returns booking ids"


def test_the_id_list_is_bounded():
    """An unbounded ARRAY_AGG on a busy TGID is a row nobody can render."""
    _, calls = _drive()
    for sql in [s for s in calls if "ARRAY_AGG" in s]:
        assert "LIMIT" in sql, sql[:200]


# ── the empty path carries the same shape ──────────────────────────────────

def test_a_booking_with_no_ids_still_returns_both_scopes():
    """A key the renderer reads and the zero path omits turns a "0 of 0" tile
    blank, which reads as a range that failed to load rather than as nothing
    to count."""
    out, _ = _drive(booking={"id": "x"})
    rec = out["recurrence"]
    assert set(rec) == {"tidvid", "tgid"}
    for scope in rec.values():
        assert scope["reviews"] == 0
        assert scope["review_ids"] == []
        assert scope["scope"]


def test_the_zero_path_and_the_live_path_agree_on_shape():
    """Two payloads with different keys is how a tile works on one review and
    is blank on the next."""
    live, _ = _drive()
    zero, _ = _drive(booking={"id": "x"})
    for scope in ("tidvid", "tgid"):
        assert set(live["recurrence"][scope]) == set(zero["recurrence"][scope])


# ── completion moves to TID+VID and VID ────────────────────────────────────

def test_completion_is_returned_for_tid_vid_as_well_as_vid():
    """Completion moves to TID+VID and VID, per "i said to put tid/vid and
    just vid"."""
    out, _ = _drive()
    assert "tidvid_completion_rate" in out
    assert "vid_completion_rate" in out


def test_the_tid_vid_completion_query_is_scoped_to_both():
    _, calls = _drive()
    ff = [s for s in calls if "fulfilment_status" in s and "tour_id = @tid" in s]
    assert ff, "no TID+VID-scoped completion query ran"
    assert all("vendor_id = @vid" in s for s in ff), \
        "the TID+VID completion query is not scoped to the vendor"


def test_the_vid_completion_query_stays_vendor_wide():
    """It answers a different question — is this vendor failing across
    everything they run — so narrowing it would lose that."""
    _, calls = _drive()
    ff = [s for s in calls
          if "fulfilment_status" in s and "vendor_id = @vid" in s
          and "tour_id = @tid" not in s and "@tgid" not in s]
    assert ff, "the vendor-wide completion query is gone"


def test_the_zero_path_declares_the_new_completion_key():
    out, _ = _drive(booking={"id": "x"})
    assert "tidvid_completion_rate" in out
    assert out["tidvid_completion_rate"] is None
