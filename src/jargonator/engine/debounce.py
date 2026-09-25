"""Per-key debouncing for Slack message updates (spec.md §6.3: at most ~1 update/s).

The first call for a key runs at once (leading edge). Calls during the following
``interval`` are coalesced, and only the latest runs when the interval ends (trailing edge).
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import timedelta

import structlog

from jargonator.clock import Clock

Work = Callable[[], Awaitable[None]]

log = structlog.get_logger()


class Debouncer:
    def __init__(self, clock: Clock, interval: float = 1.0) -> None:
        self._clock = clock
        self._interval = timedelta(seconds=interval)
        self._pending: dict[str, Work] = {}
        self._tasks: dict[str, asyncio.Task[None]] = {}

    def call(self, key: str, work: Work) -> None:
        if key in self._tasks:
            self._pending[key] = work  # coalesce: only the latest pending call survives
            return
        self._tasks[key] = asyncio.create_task(self._run(key, work))

    def active(self, key: str) -> bool:
        return key in self._tasks

    def cancel(self, key: str) -> None:
        self._pending.pop(key, None)
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()

    async def aclose(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        self._pending.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, key: str, work: Work) -> None:
        try:
            next_work: Work | None = work
            while next_work is not None:
                try:
                    await next_work()
                except Exception:
                    log.exception("debounced_work_failed", key=key)
                await self._clock.sleep_until(self._clock.now() + self._interval)
                next_work = self._pending.pop(key, None)
        finally:
            if self._tasks.get(key) is asyncio.current_task():
                del self._tasks[key]
