import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from tests.fakes.clock import FakeClock

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def test_now_and_advance() -> None:
    clock = FakeClock(T0)
    assert clock.now() == T0
    clock.advance(1.5)
    assert clock.now() == T0 + timedelta(seconds=1.5)


def test_rejects_naive_start_and_negative_advance() -> None:
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 1, 1))
    with pytest.raises(ValueError):
        FakeClock(T0).advance(-1)


async def test_sleeper_wakes_only_after_deadline() -> None:
    clock = FakeClock(T0)
    woke: list[str] = []

    async def sleeper() -> None:
        await clock.sleep_until(T0 + timedelta(seconds=10))
        woke.append("a")

    task = asyncio.create_task(sleeper())
    await clock.tick(9)
    assert woke == []
    await clock.tick(1)
    assert woke == ["a"]
    await task


async def test_past_deadline_returns_immediately() -> None:
    clock = FakeClock(T0)
    await asyncio.wait_for(clock.sleep_until(T0 - timedelta(seconds=1)), timeout=1)
    await asyncio.wait_for(clock.sleep_until(T0), timeout=1)


async def test_multiple_sleepers_wake_in_deadline_order() -> None:
    clock = FakeClock(T0)
    woke: list[int] = []

    async def sleeper(seconds: int) -> None:
        await clock.sleep_until(T0 + timedelta(seconds=seconds))
        woke.append(seconds)

    tasks = [asyncio.create_task(sleeper(s)) for s in (30, 10, 20)]
    await clock.settle()
    await clock.tick(60)
    assert woke == [10, 20, 30]
    await asyncio.gather(*tasks)


async def test_cancelled_sleeper_is_discarded() -> None:
    clock = FakeClock(T0)
    task = asyncio.create_task(clock.sleep_until(T0 + timedelta(seconds=5)))
    await clock.settle()
    task.cancel()
    await clock.settle()
    await clock.tick(10)  # must not raise InvalidStateError
    assert task.cancelled()
