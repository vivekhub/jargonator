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


def test_both_clocks_satisfy_protocol() -> None:
    clocks: list[Clock] = [SystemClock(), FakeClock(SystemClock().now().astimezone(UTC))]
    assert len(clocks) == 2
