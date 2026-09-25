"""A complete game through the Slack handler layer (spec §15 acceptance criteria)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jargonator.domain.state import GameState, PlayerStatus, RoundStatus
from jargonator.engine.timers import TimerService
from jargonator.slack import ids
from tests.engine_harness import T0, Harness, make_harness
from tests.fakes.clock import FakeClock
from tests.integration.slack_driver import SlackDriver, wait_until

pytestmark = pytest.mark.integration

JARGON = "I steward a dual-asset feline stakeholder portfolio."


@pytest.fixture
async def game(tmp_path: Path, required_env: dict[str, str]) -> AsyncIterator[SlackDriver]:
    clock = FakeClock(T0)
    timers = TimerService(clock)
    harness = await make_harness(tmp_path, scheduler=timers, clock=clock)
    timers.bind(harness.engine.dispatch_timer)
    harness.llm.jargon = [JARGON]
    yield SlackDriver(harness)
    await timers.shutdown()
    await harness.aclose()


def channel_dump(h: Harness) -> str:
    return " ".join(f"{m.text} {m.blocks}" for m in h.slack.messages_in("C1"))


async def test_full_game(game: SlackDriver) -> None:
    h = game.h
    # 1. Start via the slash command and the start modal (acceptance 1)
    await game.command("U1", "start")
    await game.submit_start("U1", guess=60, writer=90, join=0)
    for user in ("U2", "U3", "U4"):
        await game.click(user, ids.JOIN, (await h.game()).id)
    assert [p.user_id for p in await h.players()] == ["U1", "U2", "U3", "U4"]
    await game.click("U1", ids.START, (await h.game()).id)

    # 2. Round 1: the writer is DMed, submits; guessers get the jargon by DM (acceptance 2)
    h.llm.judge_scores = {"I own a pair of kitties": 95, "I have a cat": 80, "I like trains": 5}
    writer1 = await game.write_sentence("I have two cats")
    guessers = sorted(await h.guessers())
    assert writer1 not in guessers and len(guessers) == 3
    assert JARGON not in channel_dump(h)  # never in the channel before results
    for user, text in zip(
        guessers, ["I own a pair of kitties", "I have a cat", "I like trains"], strict=True
    ):
        await game.guess(user, text)  # the last one ends guessing early (acceptance 3)

    # 3. Results (acceptance 4)
    await wait_until(lambda: _state(h, GameState.AWAITING_NEXT))
    results = [m for m in h.slack.messages_in("C1") if "Round 1 results" in m.text][-1]
    shown = str(results.blocks)
    for expected in (
        "I have two cats",
        "+10",
        "+5",
        "+1",
        "95/100",
        "Leaderboard",
        "Synergy achieved.",
    ):
        assert expected in shown
    scores = {p.user_id: p.score for p in await h.players()}
    assert scores[guessers[0]] == 10 and scores[guessers[1]] == 5 and scores[guessers[2]] == 1

    # 4. Round 2: the writer times out (acceptance 6)
    await game.click("U1", ids.NEXT, (await h.game()).id)
    assert (await h.round()).number == 2
    await h.clock.tick(90)
    await wait_until(lambda: _state(h, GameState.AWAITING_NEXT))  # set after SKIPPED
    assert (await h.round()).status is RoundStatus.SKIPPED

    # 5. Round 3: everyone is far off → writer bonus (acceptance 4)
    await game.click("U1", ids.NEXT, (await h.game()).id)
    h.llm.judge_scores = {"I like trains": 20, "I own a boat": 10}
    writer3 = await game.write_sentence("I speak three languages")
    guessers3 = sorted(await h.guessers())
    await game.guess(guessers3[0], "I like trains")
    await game.guess(guessers3[1], "I own a boat")
    await h.clock.tick(60)  # the third guesser never answers: the deadline closes the round
    await wait_until(lambda: _has(h, "Round 3 results"))
    assert (await h.round()).writer_bonus_awarded is True
    assert "Nobody cracked it" in channel_dump(h)

    # 6. End via the slash command (acceptance 9)
    await game.command("U1", "end")
    final_game = await h.game()
    assert final_game.state is GameState.ENDED and final_game.end_reason == "host"
    final = [m for m in h.slack.messages_in("C1") if m.text == "🏆 Final scoreboard"][-1]
    board = str(final.blocks)
    total = sum(p.score for p in await h.players())
    assert total == 10 + 5 + 1 + 10 + 5 + 10
    assert "I own a pair of kitties" in board  # best guess highlight
    assert f"<@{writer3}> stumped the table" in board
    assert "2 rounds played" in board
    # the channel is free again
    await game.command("U2", "start")
    assert game.opened_view()["callback_id"] == ids.START_MODAL


async def test_jargon_only_reaches_guessers(game: SlackDriver) -> None:
    h = game.h
    await game.command("U1", "start")
    await game.submit_start("U1", guess=60, writer=90, join=0)
    await game.click("U2", ids.JOIN, (await h.game()).id)
    await game.click("U1", ids.START, (await h.game()).id)
    writer = await game.write_sentence("I have two cats")
    guesser = ({"U1", "U2"} - {writer}).pop()
    assert any(JARGON in str(m.blocks) for m in h.slack.dms_to(guesser))
    assert not any(JARGON in str(m.blocks) for m in h.slack.dms_to(writer))
    assert (await h.player(writer)).status is PlayerStatus.ACTIVE


async def _state(h: Harness, state: GameState) -> bool:
    return (await h.game()).state is state


async def _has(h: Harness, text: str) -> bool:
    return any(text in t for t in h.channel_texts())
