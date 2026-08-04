"""Recurrence, scoped to TID + VID, with the booking ids to go and look.

"okay keep it at tid vid only for reccurring issue insight."

The tile asks whether this VENDOR'S VERSION of the experience is failing.
Widening it to the whole TGID would answer a different question under the same
heading — which is the defect this file's neighbours record happening once
already, when the rating tiles showed TID+VID data under a TGID label.

Completion is the separate case and keeps two scopes: TID+VID and VID.

Driven through `get_insights` against a stubbed warehouse. The queries
themselves cannot be run here, so what is checked is what the panel receives.
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

def test_recurrence_is_scoped_to_tid_and_vid_only():
    out, _ = _drive()
    assert set(out["recurrence"]) == {"tidvid"}, (
        "recurrence carries a scope it was not asked for — a second heading "
        "answers a different question under the same tile")


@pytest.mark.parametrize("key", ["reviews", "reviews_total", "review_ids",
                                 "support", "support_total", "support_ids",
                                 "scope"])
def test_the_scope_carries_every_field(key):
    out, _ = _drive()
    assert key in out["recurrence"]["tidvid"], key


def test_no_tgid_scoped_recurrence_query_runs():
    """Not just absent from the payload — not run at all. A query whose result
    is discarded is a warehouse bill for a number nobody sees.

    The exclusions matter and got this wrong once. They exist to skip the two
    queries that are LEGITIMATELY TGID-scoped — the average-rating tile and
    the TGID completion rate. The first version excluded anything containing
    "rating", which also excluded the recurrence REVIEWS query, because that
    filters `r.rating <= 3`. So the test skipped precisely what it was written
    to check, and a mutation widening recurrence back to the whole TGID
    survived it.

    `avg_rating` is the rating tile's own select and appears nowhere else.
    """
    _, calls = _drive()
    recurrence_like = [c for c in calls
                       if "experience_id = @tgid" in c
                       and "avg_rating" not in c
                       and "fulfilment_status" not in c]
    assert not recurrence_like, (
        f"{len(recurrence_like)} TGID-scoped recurrence queries still run: "
        f"{[c[:120] for c in recurrence_like]}")


def test_the_exclusions_do_not_swallow_the_recurrence_queries():
    """The guard on the guard above. If the exclusions ever widen enough to
    skip every candidate, the test passes by examining nothing — which is how
    it passed the first time."""
    _, calls = _drive()
    recurrence = [c for c in calls
                  if "COUNT(DISTINCT r.booking_id)" in c
                  and "avg_rating" not in c and "fulfilment_status" not in c]
    assert recurrence, (
        "the exclusions skip every recurrence query — the test above is "
        "checking an empty list and will pass whatever the scope is")


def test_the_review_ids_are_populated():
    out, _ = _drive()
    assert out["recurrence"]["tidvid"]["review_ids"] == ["111", "222"]


def test_the_support_ids_reach_the_payload():
    """That the support ids are read out of the row and into the payload.

    NOT that the query asked for them — the stub returns ids whatever the SQL
    says, so this passes even with ARRAY_AGG removed. That is checked below,
    against the SQL itself. Saying so here because a test whose docstring
    claims a guarantee it does not provide is worse than no test: it is a
    reason not to write the one that would have caught it.
    """
    out, _ = _drive()
    assert out["recurrence"]["tidvid"]["support_ids"] == ["111", "222"]


def test_both_queries_ask_the_warehouse_for_their_ids():
    """The other end of it: the support query must actually SELECT them."""
    _, calls = _drive()
    rev = [c for c in calls if "COUNT(DISTINCT r.booking_id)" in c]
    sup = [c for c in calls if "COUNT(DISTINCT sq.booking_id)" in c]
    assert rev and sup, (len(rev), len(sup))
    assert all("ARRAY_AGG" in c for c in rev), "a reviews query returns no ids"
    assert all("ARRAY_AGG" in c for c in sup), "a support query returns no ids"


# ── booking ids ────────────────────────────────────────────────────────────

@pytest.mark.parametrize("key", ["review_ids", "support_ids"])
def test_the_ids_come_back_beside_the_counts(key):
    """"3 reviews" is a number; three booking ids are something an associate
    can open."""
    out, _ = _drive()
    assert isinstance(out["recurrence"]["tidvid"][key], list)


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

def test_a_booking_with_no_ids_still_returns_the_scope():
    """A key the renderer reads and the zero path omits turns a "0 of 0" tile
    blank, which reads as a range that failed to load rather than as nothing
    to count."""
    out, _ = _drive(booking={"id": "x"})
    rec = out["recurrence"]["tidvid"]
    assert rec["reviews"] == 0
    assert rec["review_ids"] == []
    assert rec["scope"]


def test_the_zero_path_and_the_live_path_agree_on_shape():
    """Two payloads with different keys is how a tile works on one review and
    is blank on the next."""
    live, _ = _drive()
    zero, _ = _drive(booking={"id": "x"})
    assert set(live["recurrence"]["tidvid"]) == set(zero["recurrence"]["tidvid"])


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
