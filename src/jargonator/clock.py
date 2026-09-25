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
        # Loop: asyncio.sleep runs on the monotonic clock, so a wall-clock adjustment (NTP)
        # during a long wait could otherwise wake us before ``when``.
        while (delay := (when - self.now()).total_seconds()) > 0:  # noqa: ASYNC110 (not polling)
            await asyncio.sleep(delay)
