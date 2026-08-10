"""The events timeline is sorted by its own key, not by the model's order.

WHAT WAS WRONG. `time_sort` is built with real care — ISO-8601 UTC, bookends
rescued from the booking date and the review's publication date, display
strings normalised — and then the shaped rows were returned in whatever order
the model emitted them. Nothing ever sorted by it.

That is usually chronological, which is exactly why it survived: it looks
correct run after run until one comes back out of order, and then the card
shows a sequence of events that did not happen in that sequence. On a live
card:

    [31] 2026-08-09T01:34:04+00:00  comes before  [32] 2026-08-08T22:49:00+00:00

Every case finding of the form "we did X, then the guest did Y" is built on
that order, and so is the reader's whole sense of the case.

The trace script has reported "OUT OF ORDER at N boundaries" for a while.
It was reporting on a list nothing had ever sorted.
"""
import pytest

from server.services.zendesk import sort_by_time_sort as srt


def _r(ts, what):
    return {"time_sort": ts, "label": what}


def _labels(rows):
    return [r["label"] for r in rows]


def test_rows_come_back_in_time_order():
    rows, unsorted = srt([_r("2026-08-09T01:34:04+00:00", "c"),
                          _r("2026-08-08T22:49:00+00:00", "b"),
                          _r("2026-08-01T10:00:00+00:00", "a")])
    assert _labels(rows) == ["a", "b", "c"]
    assert unsorted == 0


def test_an_already_ordered_list_is_left_alone():
    """The common case, and it must be a no-op — not a reshuffle that happens
    to land the same way."""
    given = [_r("2026-08-01T10:00:00+00:00", "a"),
             _r("2026-08-02T10:00:00+00:00", "b")]
    rows, unsorted = srt(given)
    assert _labels(rows) == ["a", "b"] and unsorted == 0


def test_a_row_with_no_key_keeps_its_neighbours():
    """IT IS NOT MOVED TO EITHER END. A row whose time nothing in the record
    supports cannot be placed on the clock, and sorting it to the top or the
    bottom would invent a placement — the exact failure the bookend rescue
    exists to prevent. It inherits the previous row's key, so it stays where
    the model put it relative to the rows around it."""
    rows, unsorted = srt([_r("2026-08-01T10:00:00+00:00", "a"),
                          _r("", "undated"),
                          _r("2026-08-02T10:00:00+00:00", "b")])
    assert _labels(rows) == ["a", "undated", "b"]
    assert unsorted == 1


def test_an_undated_row_travels_with_the_row_it_followed():
    """The point of inheriting rather than dropping to the end: if its
    neighbour moves, it moves too, because "after a" is the only thing known
    about it."""
    rows, _ = srt([_r("2026-08-09T00:00:00+00:00", "late"),
                   _r("", "after-late"),
                   _r("2026-08-01T00:00:00+00:00", "early")])
    assert _labels(rows) == ["early", "late", "after-late"]


def test_a_leading_undated_row_stays_at_the_front():
    """Nothing precedes it, so there is no key to inherit. Sorting it into the
    middle would be a placement invented out of an empty string."""
    rows, unsorted = srt([_r("", "first"),
                          _r("2026-08-02T10:00:00+00:00", "b"),
                          _r("2026-08-01T10:00:00+00:00", "a")])
    assert _labels(rows)[0] == "first"
    assert _labels(rows)[1:] == ["a", "b"]
    assert unsorted == 1


def test_rows_sharing_a_key_keep_the_order_they_arrived_in():
    """A collapsed run reaches here as several rows at one moment. A sort that
    reordered them would rewrite the model's account of what happened within
    that moment, which nothing here knows better than it does."""
    rows, _ = srt([_r("2026-08-01T10:00:00+00:00", "x"),
                   _r("2026-08-01T10:00:00+00:00", "y"),
                   _r("2026-08-01T10:00:00+00:00", "z")])
    assert _labels(rows) == ["x", "y", "z"]


def test_the_count_of_unkeyed_rows_comes_back():
    """"Sorted" over a list where three rows had no key is a different claim
    from "sorted", and the caller has to be able to tell the reader which."""
    _, unsorted = srt([_r("2026-08-01T10:00:00+00:00", "a"),
                       _r("", "x"), _r(None, "y"), _r("   ", "z")])
    assert unsorted == 3


def test_an_empty_list_is_not_an_error():
    assert srt([]) == ([], 0)


# ── the wiring ──────────────────────────────────────────────────────────────

def test_the_shaper_sorts_before_it_returns():
    """NEGATIVE-paired source assertion. The function existing and being
    correct is worth nothing if the shaping path does not call it — which was
    the state for the whole life of `time_sort`."""
    import inspect
    from server.services import zendesk
    src = inspect.getsource(zendesk)
    assert "sort_by_time_sort(out)" in src, \
        "the shaped timeline is no longer sorted before it is returned"
