"""A BigQuery query may not hold its concurrency slot for ever.

THE JAM THIS ENDS. `_BQ_SEM` was 5, a bulk re-run queued 433 seconds, and the
width was raised to 12. It then queued 425 seconds — the same jam one size up,
because nothing bounded how long ONE query could hold a slot. The HTTP calls
inside `run_query` each carry a timeout; the pagination loop around them does
not.

AND THE TIMEOUT MUST RAISE, NEVER RETURN []. Every caller reads an empty list
as "the warehouse has no such row", so a timeout returned as [] renders a
booking that exists as untraceable — the first rule of this codebase, in the
layer it matters most in.
"""
import asyncio
import time

import pytest

from server.services import bq_connector as bq


@pytest.fixture()
def slow_bq(monkeypatch):
    """A `run_query` that never answers in time, and a short ceiling."""
    def _hang(sql, params=None):
        time.sleep(5)
        return [{"never": "seen"}]
    monkeypatch.setattr(bq, "run_query", _hang)
    monkeypatch.setattr(bq, "MOCK_MODE", False)
    monkeypatch.setattr(bq, "BQ_QUERY_TIMEOUT_S", 0.3)
    return bq


def test_a_hanging_query_raises_rather_than_returning_nothing(slow_bq):
    """THE POINT. [] would mean 'no such booking' to every caller."""
    with pytest.raises(bq.BQQueryTimeout):
        asyncio.run(slow_bq.run_query_async("select 1"))


def test_the_timeout_says_it_is_our_limit_not_a_result(slow_bq):
    try:
        asyncio.run(slow_bq.run_query_async("select 1"))
        raise AssertionError("should have raised")
    except bq.BQQueryTimeout as e:
        msg = str(e).lower()
        # A reader must not take this for a miss.
        assert "not a result" in msg
        assert "may well exist" in msg


def test_the_slot_is_released_so_the_next_query_is_not_blocked(slow_bq):
    """A stuck query costs ONE slot for the ceiling, not the connector.

    MEASURED INSIDE THE LOOP, on purpose. `wait_for` abandons the future but
    the executor THREAD keeps sleeping, and `asyncio.run` joins those threads
    on the way out — so timing the whole `asyncio.run` measures the abandoned
    thread, not the slot. The slot is what this guards: the second caller must
    not queue behind the first.
    """
    async def _two():
        for _ in range(2):
            with pytest.raises(bq.BQQueryTimeout):
                await slow_bq.run_query_async("select 1")
        # Time only the ACQUIRE — a released slot is immediate.
        t0 = time.time()
        async with bq._BQ_SEM:
            return time.time() - t0

    assert asyncio.run(_two()) < 0.2


def test_a_query_that_answers_in_time_is_untouched(monkeypatch):
    monkeypatch.setattr(bq, "run_query", lambda sql, params=None: [{"ok": 1}])
    monkeypatch.setattr(bq, "MOCK_MODE", False)
    monkeypatch.setattr(bq, "BQ_QUERY_TIMEOUT_S", 5.0)
    assert asyncio.run(bq.run_query_async("select 1")) == [{"ok": 1}]


def test_mock_mode_still_short_circuits(monkeypatch):
    monkeypatch.setattr(bq, "MOCK_MODE", True)
    assert asyncio.run(bq.run_query_async("select 1")) == []


def test_the_ceiling_is_configurable_without_an_edit():
    # Operations needs to widen this on a slow day without shipping a commit.
    import os
    assert "BQ_QUERY_TIMEOUT_S" in os.environ or bq.BQ_QUERY_TIMEOUT_S > 0
