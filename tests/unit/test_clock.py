import asyncio
import time
from datetime import UTC, timedelta

from jargonator.clock import Clock, SystemClock
from tests.fakes.clock import FakeClock


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


async def test_system_clock_sleep_until() -> None:
    clock = SystemClock()
    start = time.monotonic()
    await clock.sleep_until(clock.now() + timedelta(milliseconds=50))
    assert time.monotonic() - start >= 0.04
    await asyncio.wait_for(clock.sleep_until(clock.now() - timedelta(seconds=5)), timeout=1)


async def test_system_clock_resleeps_if_woken_early(monkeypatch: object) -> None:
    """Review finding 6: a wake-up before the wall-clock deadline must sleep again."""
    clock = SystemClock()
    target = clock.now() + timedelta(seconds=10)
    sleeps: list[float] = []
    readings = iter([target - timedelta(seconds=10), target - timedelta(seconds=3), target])

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    clock.now = lambda: next(readings)  # type: ignore[method-assign]
    import jargonator.clock as clock_module

    monkeypatch.setattr(clock_module.asyncio, "sleep", fake_sleep)  # type: ignore[attr-defined]
    await clock.sleep_until(target)
    assert sleeps == [10.0, 3.0]


def test_both_clocks_satisfy_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FakeClock(SystemClock().now().astimezone(UTC))]
    assert len(clocks) == 2
