"""Per-loop asyncio primitives.

asyncio.Semaphore binds to the event loop that FIRST awaits it and raises
"bound to a different event loop" from every other loop afterwards. A
module-level semaphore is therefore poisoned by whichever loop touches it
first - a pipeline re-run executing in a fresh loop crashed all fifteen
BigQuery insights queries at once this way, which nulled the experience-page
data every downstream verification depends on.

LoopLocalSemaphore keys a real semaphore by the running loop: the
concurrency cap applies within each loop (which is where concurrency
exists), and no loop ever sees another loop's binding. Loops are held
weakly so a finished loop's semaphore is dropped with it.
"""
import asyncio
from weakref import WeakKeyDictionary


class LoopLocalSemaphore:
    def __init__(self, value: int):
        self._value = value
        self._per_loop: WeakKeyDictionary = WeakKeyDictionary()

    def _get(self) -> asyncio.Semaphore:
        loop = asyncio.get_running_loop()
        sem = self._per_loop.get(loop)
        if sem is None:
            sem = asyncio.Semaphore(self._value)
            self._per_loop[loop] = sem
        return sem

    # Within one `async with` block the running loop cannot change, so
    # __aexit__ re-deriving the semaphore always releases the one acquired.
    async def __aenter__(self):
        await self._get().acquire()
        return self

    async def __aexit__(self, *exc):
        self._get().release()
        return False
