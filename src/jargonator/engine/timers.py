"""The real Scheduler: one asyncio task per pending (game, timer kind) (spec.md §9).

Deadlines are persisted by the engine before scheduling, so this is only an in-memory
cache of what the DB says. After a restart, ``GameEngine.recover`` re-schedules them.
"""

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from datetime import datetime

import structlog

from jargonator.clock import Clock
from jargonator.engine.scheduler import TimerKind

Dispatch = Callable[[str, TimerKind, str | None], Awaitable[None]]
Key = tuple[str, TimerKind]

log = structlog.get_logger()


class TimerService:
    def __init__(self, clock: Clock) -> None:
        self._clock = clock
        self._dispatch: Dispatch | None = None
        self._tasks: dict[Key, tuple[asyncio.Task[None], datetime]] = {}

    def bind(self, dispatch: Dispatch) -> None:
        """Set the callback (``GameEngine.dispatch_timer``). This breaks the construction
        cycle: the engine needs the scheduler, and the scheduler needs the engine."""
        self._dispatch = dispatch

    def schedule(self, game_id: str, kind: TimerKind, when: datetime, round_id: str | None) -> None:
        if self._dispatch is None:
            raise RuntimeError("TimerService.bind() must be called before scheduling")
        self.cancel(game_id, kind)
        key = (game_id, kind)
        task = asyncio.create_task(self._run(key, when, round_id), name=f"timer:{game_id}:{kind}")
        self._tasks[key] = (task, when)

    def cancel(self, game_id: str, kind: TimerKind) -> None:
        entry = self._tasks.pop((game_id, kind), None)
        if entry is not None:
            entry[0].cancel()

    def cancel_all(self, game_id: str) -> None:
        for key in [k for k in self._tasks if k[0] == game_id]:
            self.cancel(*key)

    def pending(self) -> dict[Key, datetime]:
        return {key: when for key, (_, when) in self._tasks.items()}

    async def shutdown(self) -> None:
        tasks = [task for task, _ in self._tasks.values()]
        self._tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self, key: Key, when: datetime, round_id: str | None) -> None:
        await self._clock.sleep_until(when)
        # Deregister before dispatching, so the handler can reschedule this same kind
        # (e.g. the idle timer) without cancelling itself.
        entry = self._tasks.get(key)
        if entry is not None and entry[0] is asyncio.current_task():
            del self._tasks[key]
        game_id, kind = key
        assert self._dispatch is not None
        try:
            await self._dispatch(game_id, kind, round_id)
        except Exception:
            log.exception("timer_dispatch_failed", game_id=game_id, kind=str(kind))
