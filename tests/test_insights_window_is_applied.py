"""Every windowed figure moves when the window moves — checked, not asserted.

"no the booking count etc or even support contact will not be the same in the
different date ranges. you havent checked that. the denominator will change."

Right, and the earlier check only looked at the two totals. This drives
get_insights at three windows against a fake warehouse that answers with the
window it was asked for, and compares EVERY numeric field in the payload. A
field that does not move is either genuinely window-independent — the same-day
tiles are, by definition — or it is a query someone forgot to bound, and those
two have to be told apart by name rather than by eye.
"""
import asyncio
import re

import pytest


@pytest.fixture()
def bq(monkeypatch):
    """A warehouse whose every answer is the window in the SQL.

    So any figure that tracks the window comes back different per window, and
    any figure that does not is visible immediately.
    """
    import server.services.insights as I
    import server.services.bq_connector as BQ

    seen = []

    async def fake(sql, params=None):
        seen.append(sql)
        m = re.search(r"INTERVAL (\d+) DAY", sql)
        n = int(m.group(1)) if m else 0
        # The numerator and denominator must not scale together, or every
        # derived ratio comes out constant and the test would be measuring
        # this fixture rather than the code. "Similar" queries are the ones
        # filtered to the L2 variants or the tag list.
        similar = "@l2v" in sql or "@tags" in sql or "@pat0" in sql
        c = (n // 3) if similar else n
        return [{"c": c, "avg_rating": round(1 + n / 100, 2), "n_ratings": n * 10,
                 "completed": n, "completed_by_booking_status": n,
                 "unfulfilled": 1, "total": n * 2 + 3, "ids": [],
                 "status": "Cancelled", "ctype": "Cancelled By Vendor"}]

    monkeypatch.setattr(BQ, "run_query_async", fake)
    monkeypatch.setattr(I, "is_live", lambda k: True)
    monkeypatch.setattr(I, "MOCK_MODE", False)
    return seen


BOOKING = {"tid": "43605", "vid": "4040", "tgid": "22238",
           "date_of_visit": "2026-06-01"}

# Anchored on the visit date, so they are the same three tiles whatever window
# is picked. Not unbounded queries — the opposite, deliberately bounded to one
# day. Named here so "did not move" can be read as intended rather than as a
# miss.
SAME_DAY_ON_PURPOSE = {"same_day", "same_day_same_vid", "sameDaySameVidIssues",
                       "ff_same_day"}


def _run(window):
    import server.services.insights as I
    return asyncio.run(I.get_insights(BOOKING, "Operations Issue",
                                      "Ticket Issues", window=window))


def test_the_denominators_move_with_the_window(bq):
    a, b = _run("7d"), _run("90d")
    for key in ("total_reviews_30d", "total_support_queries_30d",
                "total_bookings_30d"):
        assert a[key] != b[key], (
            f"{key} is {a[key]} at 7d and {b[key]} at 90d — the denominator is "
            f"not bounded by the picked window")
        assert (a[key], b[key]) == (7, 90), (key, a[key], b[key])


def test_the_ratings_move_with_the_window(bq):
    a, b = _run("7d"), _run("90d")
    assert a["rating_tgid"]["avg"] != b["rating_tgid"]["avg"]
    assert a["rating_tidvid"]["avg"] != b["rating_tidvid"]["avg"]
    assert a["rating_tgid"]["n"] != b["rating_tgid"]["n"], \
        "the rating COUNT is not windowed, so an average is quoted over the "\
        "wrong population"


def test_the_completion_rates_move_with_the_window(bq):
    a, b = _run("7d"), _run("90d")
    assert a["ff_vid"]["total"] != b["ff_vid"]["total"]
    assert a["ff_tgid"]["total"] != b["ff_tgid"]["total"]


def test_every_windowed_query_carries_the_interval(bq):
    """Not by reading the file. Every SQL string the run issued is inspected,
    and the ones with no INTERVAL are named."""
    bq.clear()
    _run("30d")
    unbounded = [s for s in bq
                 if "COUNT" in s.upper() or "AVG(" in s.upper()]
    missing = [s for s in unbounded
               if "INTERVAL" not in s and "experience_date) = " not in s]
    assert not missing, (
        f"{len(missing)} counting quer(ies) have neither a window nor a "
        f"same-day anchor:\n\n" + "\n\n---\n\n".join(s[:300] for s in missing))


def test_only_the_same_day_tiles_hold_still(bq):
    """The complement of the test above: anything that does NOT move must be
    one of the tiles that is not supposed to. A new unbounded figure fails
    here rather than being spotted on a card."""
    a, b = _run("7d"), _run("90d")
    held = []
    for k, v in a.items():
        if k.startswith("_") or k in SAME_DAY_ON_PURPOSE:
            continue
        if isinstance(v, (int, float)) and v == b.get(k) and v not in (0, None):
            held.append(f"{k}={v}")
    assert not held, (
        "these figures are identical at 7d and 90d, so they are not being "
        "measured over the picked window:\n  " + "\n  ".join(held))


def test_the_window_label_says_what_was_measured(bq):
    a = _run("7d")
    assert a["_window_days"] == 7
    assert "7 days" in a["_window_label"]
    assert a["_anchored_on"] == "2026-06-01", \
        "the window is anchored on today rather than on the visit date"


def test_an_unknown_window_falls_back_and_is_reported_not_guessed(bq):
    """A typo in the query string must not silently become 30d with a label
    claiming otherwise — the label and the figures have to agree."""
    a = _run("nonsense")
    assert a["_window_days"] == 30
    assert "30 days" in a["_window_label"]
    assert a["total_bookings_30d"] == 30, \
        "the label says 30 days and the query asked for something else"
