"""Time abstraction so timers and deadlines are testable (spec.md §9)."""

import asyncio
from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Source of the current time and of deadline-based sleeping."""

    def now(self) -> datetime:
        """Return the current time as a timezone-aware UTC datetime."""
        ...

    async def sleep_until(self, when: datetime) -> None:
        """Suspend until ``when`` (returns immediately if it has already passed)."""
        ...


class SystemClock:
    """The real wall clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)

    async def sleep_until(self, when: datetime) -> None:
        delay = (when - self.now()).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
