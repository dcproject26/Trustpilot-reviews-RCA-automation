"""LoopLocalSemaphore must survive what killed the insights queries: being
used from one event loop, then from a different one. A plain module-level
asyncio.Semaphore raises "bound to a different event loop" on the second."""
import asyncio

from server.aio import LoopLocalSemaphore


def test_survives_two_event_loops():
    sem = LoopLocalSemaphore(2)

    async def use():
        async with sem:
            await asyncio.sleep(0)
        return "ok"

    # Two separate asyncio.run calls = two distinct loops, the re-run shape.
    assert asyncio.run(use()) == "ok"
    assert asyncio.run(use()) == "ok"


def test_plain_semaphore_actually_fails_across_loops():
    # Documents WHY the wrapper exists. Binding happens only when a task has
    # to WAIT - an uncontended acquire never touches the loop. That is
    # exactly why insights died (15 queries against cap 5 guarantees
    # waiters) while uncontended paths never showed the bug. If a Python
    # version ever makes this pass, the wrapper can be retired.
    plain = asyncio.Semaphore(1)

    async def contended():
        async def hold():
            async with plain:
                await asyncio.sleep(0.01)
        await asyncio.gather(hold(), hold())   # second task must wait -> binds

    asyncio.run(contended())
    try:
        asyncio.run(contended())
    except RuntimeError as e:
        assert "different event loop" in str(e) or "attached to a different loop" in str(e)
    else:
        raise AssertionError(
            "plain Semaphore no longer fails across loops - wrapper may be retirable")


def test_wrapper_survives_contention_across_loops():
    # The wrapper under the exact shape that kills the plain semaphore.
    sem = LoopLocalSemaphore(1)

    async def contended():
        async def hold():
            async with sem:
                await asyncio.sleep(0.01)
        await asyncio.gather(hold(), hold())

    asyncio.run(contended())
    asyncio.run(contended())


def test_cap_still_enforced_within_a_loop():
    sem = LoopLocalSemaphore(1)
    running = 0
    peak = 0

    async def work():
        nonlocal running, peak
        async with sem:
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0.01)
            running -= 1

    async def main():
        await asyncio.gather(*[work() for _ in range(5)])

    asyncio.run(main())
    assert peak == 1, f"cap 1 but {peak} ran concurrently"
