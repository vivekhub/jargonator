"""A manually advanced clock for deterministic timer tests."""

import asyncio
import heapq
import itertools
from datetime import datetime, timedelta


class FakeClock:
    """Time only moves when a test calls ``advance``/``tick``.

    Sleepers are woken in deadline order (ties in the order they started sleeping).
    """

    def __init__(self, start: datetime) -> None:
        if start.tzinfo is None:
            raise ValueError("FakeClock needs a timezone-aware start time")
        self._now = start
        self._seq = itertools.count()
        self._sleepers: list[tuple[datetime, int, asyncio.Future[None]]] = []

    def now(self) -> datetime:
        return self._now

    async def sleep_until(self, when: datetime) -> None:
        if when <= self._now:
            return
        future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        heapq.heappush(self._sleepers, (when, next(self._seq), future))
        await future

    def advance(self, seconds: float) -> None:
        """Move time forward and release every sleeper whose deadline has passed."""
        if seconds < 0:
            raise ValueError("Time cannot go backwards")
        self._now += timedelta(seconds=seconds)
        while self._sleepers and self._sleepers[0][0] <= self._now:
            _, _, future = heapq.heappop(self._sleepers)
            if not future.done():
                future.set_result(None)

    async def settle(self, rounds: int = 20) -> None:
        """Yield to the event loop until woken tasks have had a chance to run."""
        for _ in range(rounds):
            await asyncio.sleep(0)

    async def tick(self, seconds: float) -> None:
        """``advance`` then ``settle``: the usual way to move time in async tests."""
        self.advance(seconds)
        await self.settle()

    @property
    def pending_sleepers(self) -> int:
        return sum(1 for *_, f in self._sleepers if not f.done())
