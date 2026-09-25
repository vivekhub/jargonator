"""Restart recovery (spec §9, acceptance criterion 8).

Each test drives engine A to some state, abandons it without ending the game (like a
crash), then builds engine B on the same database with a fresh TimerService and clock,
calls recover(), and checks the game carries on correctly.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import timedelta
from pathlib import Path

import pytest

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.timers import TimerService
from jargonator.llm.client import LLMError
from tests.engine_harness import T0, Harness, make_harness
from tests.fakes.clock import FakeClock

pytestmark = pytest.mark.integration


async def wait_until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while not await check():  # noqa: ASYNC110 (test helper: polling is the point)
            await asyncio.sleep(0.01)


class Restart:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.harnesses: list[Harness] = []
        self.timers: list[TimerService] = []

    async def engine_a(self) -> Harness:
        harness = await make_harness(self.tmp_path)
        self.harnesses.append(harness)
        return harness

    async def engine_b(self, seconds_later: float) -> Harness:
        clock = FakeClock(T0 + timedelta(seconds=seconds_later))
        timers = TimerService(clock)
        harness = await make_harness(self.tmp_path, scheduler=timers, clock=clock)
        timers.bind(harness.engine.dispatch_timer)
        self.harnesses.append(harness)
        self.timers.append(timers)
        harness.extra["game_ids"] = self.harnesses[0].extra.get("game_ids", [])
        await harness.engine.recover()
        await clock.settle()
        return harness

    async def aclose(self) -> None:
        for timers in self.timers:
            await timers.shutdown()
        for harness in self.harnesses:
            await harness.aclose()


@pytest.fixture
async def restart(tmp_path: Path, required_env: dict[str, str]) -> AsyncIterator[Restart]:
    r = Restart(tmp_path)
    yield r
    await r.aclose()


def results_posts(harness: Harness) -> list[str]:
    return [t for t in harness.channel_texts() if "results" in t and "Round" in t]


async def test_guess_deadline_passed_during_downtime(restart: Restart) -> None:
    a = await restart.engine_a()
    await a.started("U1", "U2", "U3")
    await a.write()
    rnd = await a.round()
    guessers = sorted(await a.guessers())
    await a.engine.submit_guess(guessers[0], rnd.id, "I own kitties")

    b = await restart.engine_b(seconds_later=600)
    # Wait for the post itself: the state flips (in the same lock) just before posting.
    await wait_until(lambda: _has(b, "Round 1 results"))
    assert len(results_posts(b)) == 1
    assert (await b.player(guessers[0])).score == 10
    assert (await b.round()).status is RoundStatus.COMPLETED


async def test_writer_timeout_still_fires_on_time(restart: Restart) -> None:
    a = await restart.engine_a()
    await a.started("U1", "U2")

    b = await restart.engine_b(seconds_later=30)
    assert (await b.game()).state is GameState.AWAITING_SENTENCE
    await b.clock.tick(59)
    await asyncio.sleep(0.05)
    assert (await b.game()).state is GameState.AWAITING_SENTENCE
    await b.clock.tick(1)
    await wait_until(lambda: _state_is(b, GameState.AWAITING_NEXT))
    assert (await b.round()).status is RoundStatus.SKIPPED


async def test_crash_after_scoring_does_not_double_award(restart: Restart) -> None:
    a = await restart.engine_a()
    entered, _release = a.llm.hold("judge")
    await a.started("U1", "U2")
    await a.write()
    rnd = await a.round()
    guesser = (await a.guessers())[0]
    stuck = asyncio.create_task(a.engine.submit_guess(guesser, rnd.id, "I own kitties"))
    await asyncio.wait_for(entered.wait(), 3)
    # Simulate "scored, then crashed before posting": finalize directly, then abandon A.
    game = await a.game()
    from jargonator.db.repo import GuessResult

    guess = (await a.repo.get_guesses(rnd.id))[0]
    await a.repo.finalize_round_scoring(
        game_id=game.id,
        round_id=rnd.id,
        results=[GuessResult(guess.id, 95, 1, 10, False)],
        awards={guesser: (10, True)},
        writer_bonus_awarded=False,
        quip=None,
        host_claim_at=T0 + timedelta(minutes=5),
        next_state=GameState.AWAITING_NEXT,
        now=T0,
    )
    stuck.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stuck

    b = await restart.engine_b(seconds_later=5)
    await wait_until(lambda: _has(b, "Round 1 results"))
    assert (await b.player(guesser)).score == 10  # not 20
    assert len(results_posts(b)) == 1
    assert b.llm.calls_to("judge") == []  # nothing re-judged


async def test_crash_during_generation_resumes(restart: Restart) -> None:
    a = await restart.engine_a()
    entered, _release = a.llm.hold("generate_jargon")
    rnd = await a.started("U1", "U2")
    stuck = asyncio.create_task(
        a.engine.submit_sentence(rnd.writer_user_id, rnd.id, "I have two cats")
    )
    await asyncio.wait_for(entered.wait(), 3)
    stuck.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await stuck
    assert (await a.game()).state is GameState.GENERATING

    b = await restart.engine_b(seconds_later=5)
    await wait_until(lambda: _state_is(b, GameState.GUESSING))
    assert len(b.llm.calls_to("generate_jargon")) == 1


async def test_restart_during_llm_retry_wait(restart: Restart) -> None:
    a = await restart.engine_a()
    a.llm.fail("generate_jargon", LLMError("down"), times=1)
    await a.started("U1", "U2")
    await a.write()
    assert (await a.round()).llm_retry_at == T0 + timedelta(seconds=30)

    b = await restart.engine_b(seconds_later=10)
    b.llm.fail("generate_jargon", LLMError("still down"), times=1)
    await b.clock.tick(19)
    await asyncio.sleep(0.05)
    assert b.llm.calls_to("generate_jargon") == []  # not yet: the retry is at T0+30
    await b.clock.tick(1)
    await wait_until(lambda: _state_is(b, GameState.ENDED))
    assert (await b.game()).end_reason == "llm_failure"


async def test_lobby_and_idle_timers_are_restored(restart: Restart) -> None:
    a = await restart.engine_a()
    await a.create("U1", join_window=120)
    await a.engine.join("C1", "U2")

    b = await restart.engine_b(seconds_later=60)
    await b.clock.tick(60)
    await wait_until(lambda: _state_is(b, GameState.AWAITING_SENTENCE))


async def _state_is(harness: Harness, state: GameState) -> bool:
    return (await harness.game()).state is state


async def _has(harness: Harness, text: str) -> bool:
    return any(text in t for t in harness.channel_texts())
