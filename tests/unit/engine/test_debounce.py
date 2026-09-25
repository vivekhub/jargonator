from datetime import UTC, datetime

from jargonator.engine.debounce import Debouncer
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 1, 1, tzinfo=UTC)


async def test_leading_call_then_one_trailing_call_per_interval() -> None:
    clock = FakeClock(T0)
    debouncer = Debouncer(clock, interval=1.0)
    runs: list[int] = []

    for i in range(10):

        async def work(i: int = i) -> None:
            runs.append(i)

        debouncer.call("k", work)
    await clock.settle()
    assert runs == [0]  # leading edge runs immediately
    await clock.tick(1)
    assert runs == [0, 9]  # trailing edge runs only the latest
    await clock.tick(1)
    assert runs == [0, 9]  # nothing pending: the cooldown ends
    assert not debouncer.active("k")


async def test_keys_are_independent() -> None:
    clock = FakeClock(T0)
    debouncer = Debouncer(clock)
    runs: list[str] = []

    async def a() -> None:
        runs.append("a")

    async def b() -> None:
        runs.append("b")

    debouncer.call("x", a)
    debouncer.call("y", b)
    await clock.settle()
    assert sorted(runs) == ["a", "b"]


async def test_cancel_drops_pending_work() -> None:
    clock = FakeClock(T0)
    debouncer = Debouncer(clock)
    runs: list[int] = []

    async def work() -> None:
        runs.append(1)

    debouncer.call("k", work)
    debouncer.call("k", work)
    await clock.settle()
    debouncer.cancel("k")
    await clock.tick(5)
    assert runs == [1]


async def test_errors_are_swallowed() -> None:
    clock = FakeClock(T0)
    debouncer = Debouncer(clock)

    async def boom() -> None:
        raise RuntimeError("slack down")

    debouncer.call("k", boom)
    await clock.settle()
    await clock.tick(1)
    assert not debouncer.active("k")


async def test_aclose_cancels_everything() -> None:
    clock = FakeClock(T0)
    debouncer = Debouncer(clock)

    async def work() -> None:
        return None

    debouncer.call("k", work)
    debouncer.call("k", work)
    await clock.settle()
    await debouncer.aclose()
    assert not debouncer.active("k")
