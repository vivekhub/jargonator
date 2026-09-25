"""The real TimerService drives the engine on a fake clock (spec §9)."""

from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.timers import TimerService
from jargonator.llm.client import LLMError
from tests.engine_harness import T0, Harness, make_harness
from tests.fakes.clock import FakeClock

pytestmark = pytest.mark.integration


async def wait_until(check: Callable[[], Awaitable[bool]]) -> None:
    import asyncio

    async with asyncio.timeout(5):
        while not await check():  # noqa: ASYNC110 (test helper: polling is the point)
            await asyncio.sleep(0.01)


@pytest.fixture
async def timed(tmp_path: Path, required_env: dict[str, str]) -> AsyncIterator[Harness]:
    clock = FakeClock(T0)
    timers = TimerService(clock)
    harness = await make_harness(tmp_path, scheduler=timers, clock=clock)
    timers.bind(harness.engine.dispatch_timer)
    yield harness
    await timers.shutdown()
    await harness.aclose()


async def posted(harness: Harness, text: str) -> bool:
    return any(text in t for t in harness.channel_texts())


async def state_is(harness: Harness, state: GameState) -> bool:
    return (await harness.game()).state is state


async def test_timers_drive_a_full_round(timed: Harness) -> None:
    await timed.started("U1", "U2", "U3")
    await timed.clock.tick(90)  # writer deadline passes
    await wait_until(lambda: state_is(timed, GameState.AWAITING_NEXT))
    assert (await timed.round()).status is RoundStatus.SKIPPED

    await timed.engine.next_round("C1", (await timed.game()).host_user_id)
    await timed.write()
    await timed.clock.tick(60)  # guess deadline passes with no guesses
    await wait_until(lambda: posted(timed, "Round 2 results"))
    assert (await timed.round()).status is RoundStatus.COMPLETED


async def test_llm_retry_timer_fires(timed: Harness) -> None:
    timed.llm.fail("judge", LLMError("down"), times=1)
    await timed.started("U1", "U2")
    await timed.write()
    rnd = await timed.round()
    await timed.engine.submit_guess((await timed.guessers())[0], rnd.id, "I own kitties")
    assert (await timed.game()).state is GameState.JUDGING
    await timed.clock.tick(30)
    await wait_until(lambda: state_is(timed, GameState.AWAITING_NEXT))
    assert (await timed.round()).status is RoundStatus.COMPLETED


async def test_host_claim_and_idle_timers(timed: Harness) -> None:
    await timed.started("U1", "U2")
    await timed.clock.tick(90)
    await wait_until(lambda: state_is(timed, GameState.AWAITING_NEXT))
    await timed.clock.tick(300)  # claim host becomes available
    await timed.clock.tick(7200)  # 2 h idle
    await wait_until(lambda: state_is(timed, GameState.ENDED))
    assert (await timed.game()).end_reason == "idle"
