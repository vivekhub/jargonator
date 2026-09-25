import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jargonator.engine.scheduler import TimerKind
from jargonator.engine.timers import TimerService
from tests.fakes.clock import FakeClock

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def make() -> tuple[TimerService, FakeClock, list[tuple[str, TimerKind, str | None]]]:
    clock = FakeClock(T0)
    fired: list[tuple[str, TimerKind, str | None]] = []

    async def dispatch(game_id: str, kind: TimerKind, round_id: str | None) -> None:
        fired.append((game_id, kind, round_id))

    timers = TimerService(clock)
    timers.bind(dispatch)
    return timers, clock, fired


async def test_fires_at_the_deadline() -> None:
    timers, clock, fired = make()
    timers.schedule("g1", TimerKind.GUESS_DEADLINE, T0 + timedelta(seconds=60), "r1")
    await clock.tick(59)
    assert fired == []
    await clock.tick(1)
    assert fired == [("g1", TimerKind.GUESS_DEADLINE, "r1")]
    assert timers.pending() == {}


async def test_past_deadline_fires_immediately() -> None:
    timers, clock, fired = make()
    timers.schedule("g1", TimerKind.IDLE, T0 - timedelta(seconds=5), None)
    await clock.settle()
    assert fired == [("g1", TimerKind.IDLE, None)]


async def test_rescheduling_replaces() -> None:
    timers, clock, fired = make()
    timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=10), None)
    timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=20), None)
    await clock.tick(15)
    assert fired == []
    await clock.tick(5)
    assert fired == [("g1", TimerKind.IDLE, None)]


async def test_cancel_and_cancel_all() -> None:
    timers, clock, fired = make()
    timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=10), None)
    timers.schedule("g1", TimerKind.LOBBY, T0 + timedelta(seconds=10), None)
    timers.schedule("g2", TimerKind.LOBBY, T0 + timedelta(seconds=10), None)
    timers.cancel("g1", TimerKind.IDLE)
    timers.cancel_all("g2")
    await clock.tick(10)
    assert fired == [("g1", TimerKind.LOBBY, None)]


async def test_dispatch_can_reschedule_its_own_kind() -> None:
    clock = FakeClock(T0)
    timers = TimerService(clock)
    fired: list[datetime] = []

    async def dispatch(game_id: str, kind: TimerKind, round_id: str | None) -> None:
        fired.append(clock.now())
        if len(fired) == 1:
            timers.schedule(game_id, kind, clock.now() + timedelta(seconds=10), None)

    timers.bind(dispatch)
    timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=10), None)
    await clock.tick(10)
    await clock.tick(10)
    assert fired == [T0 + timedelta(seconds=10), T0 + timedelta(seconds=20)]


async def test_dispatch_errors_are_logged_not_raised(caplog: pytest.LogCaptureFixture) -> None:
    clock = FakeClock(T0)
    timers = TimerService(clock)

    async def boom(game_id: str, kind: TimerKind, round_id: str | None) -> None:
        raise RuntimeError("handler bug")

    timers.bind(boom)
    timers.schedule("g1", TimerKind.IDLE, T0, None)
    await clock.settle()
    timers.schedule("g1", TimerKind.LOBBY, T0 + timedelta(seconds=1), None)
    await clock.tick(1)  # the service still works after a failing handler
    assert timers.pending() == {}


async def test_shutdown_cancels_everything() -> None:
    timers, clock, fired = make()
    timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=10), None)
    await timers.shutdown()
    await clock.tick(10)
    assert fired == [] and timers.pending() == {}


async def test_unbound_service_refuses_to_schedule() -> None:
    with pytest.raises(RuntimeError, match="bind"):
        TimerService(FakeClock(T0)).schedule("g1", TimerKind.IDLE, T0, None)


def test_pending_introspection() -> None:
    async def run() -> None:
        timers, _, _ = make()
        timers.schedule("g1", TimerKind.IDLE, T0 + timedelta(seconds=10), None)
        assert timers.pending() == {("g1", TimerKind.IDLE): T0 + timedelta(seconds=10)}
        await timers.shutdown()

    asyncio.run(run())


async def test_timer_tasks_do_not_inherit_the_callers_log_context() -> None:
    import structlog

    clock = FakeClock(T0)
    timers = TimerService(clock)
    seen: list[dict[str, object]] = []

    async def dispatch(game_id: str, kind: TimerKind, round_id: str | None) -> None:
        seen.append(structlog.contextvars.get_contextvars())

    timers.bind(dispatch)
    structlog.contextvars.bind_contextvars(user_id="U2", channel_id="DU2")
    timers.schedule("g1", TimerKind.IDLE, T0, None)
    structlog.contextvars.clear_contextvars()
    await clock.settle()
    assert seen == [{}]
