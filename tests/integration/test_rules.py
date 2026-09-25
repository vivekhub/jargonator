"""Game rules through the Slack handler layer (spec §3.2, §3.4, §3.10, §3.11)."""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jargonator.domain.state import GameState, PlayerStatus
from jargonator.engine.timers import TimerService
from jargonator.llm.client import LLMError
from jargonator.slack import ids
from tests.engine_harness import T0, make_harness
from tests.fakes.clock import FakeClock
from tests.integration.slack_driver import SlackDriver, wait_until

pytestmark = pytest.mark.integration


@pytest.fixture
async def slack(tmp_path: Path, required_env: dict[str, str]) -> AsyncIterator[SlackDriver]:
    clock = FakeClock(T0)
    timers = TimerService(clock)
    harness = await make_harness(tmp_path, scheduler=timers, clock=clock)
    timers.bind(harness.engine.dispatch_timer)
    yield SlackDriver(harness)
    await timers.shutdown()
    await harness.aclose()


async def open_game(slack: SlackDriver, *users: str, channel: str = "C1") -> None:
    slack.h.channel = channel
    await slack.command(users[0], "start", channel)
    await slack.submit_start(users[0], guess=60, writer=90, join=0)
    for user in users[1:]:
        await slack.command(user, "join", channel)


async def test_one_game_per_channel_and_per_player(slack: SlackDriver) -> None:
    await open_game(slack, "U1", "U2")
    await slack.command("U3", "start")
    assert "already a game" in slack.last_response("U3")
    await open_game(slack, "U5", channel="C2")
    await slack.command("U2", "join", "C2")
    assert "<#C1>" in slack.last_response("U2")


async def test_three_misses_then_rejoin_keeps_score(slack: SlackDriver) -> None:
    h = slack.h
    await open_game(slack, "U1", "U2", "U3")
    game = await h.game()
    await slack.click("U1", ids.START, game.id)
    victim = (await h.round()).writer_user_id
    await h.repo.add_points(game.id, victim, 7, round_win=False)
    await h.repo.update_player(game.id, victim, consecutive_misses=2)
    await h.clock.tick(90)
    await wait_until(lambda: _status(slack, victim, PlayerStatus.INACTIVE))
    await slack.command(victim, "join")
    player = await h.player(victim)
    assert (player.status, player.score) == (PlayerStatus.ACTIVE, 7)


async def test_host_leaves_then_transfer(slack: SlackDriver) -> None:
    await open_game(slack, "U1", "U2", "U3")
    await slack.command("U1", "leave")
    assert (await slack.h.game()).host_user_id == "U2"


async def test_claim_host_after_five_minutes(slack: SlackDriver) -> None:
    h = slack.h
    await open_game(slack, "U1", "U2", "U3")
    await slack.click("U1", ids.START, (await h.game()).id)
    await h.clock.tick(90)  # writer times out
    await wait_until(lambda: _state(slack, GameState.AWAITING_NEXT))
    round_id = (await h.round()).id
    await slack.click("U2", ids.CLAIM_HOST, round_id)
    assert "5 minutes" in slack.last_response("U2")
    await h.clock.tick(300)
    await slack.click("U2", ids.CLAIM_HOST, round_id)
    assert (await h.game()).host_user_id == "U2"


async def test_judge_failing_twice_ends_the_game(slack: SlackDriver) -> None:
    h = slack.h
    h.llm.fail("judge", LLMError("down"), times=2)
    await open_game(slack, "U1", "U2")
    await slack.click("U1", ids.START, (await h.game()).id)
    await slack.write_sentence("I have two cats")
    guesser = (await h.guessers())[0]
    await slack.guess(guesser, "I own kitties")
    assert any("retrying in 30 seconds" in t for t in h.channel_texts())
    await h.clock.tick(30)
    await wait_until(lambda: _state(slack, GameState.ENDED))
    texts = h.channel_texts()
    assert any("can't continue" in t for t in texts)
    assert any(t == "🏆 Final scoreboard" for t in texts)
    assert (await h.game()).end_reason == "llm_failure"


async def test_non_host_cannot_end(slack: SlackDriver) -> None:
    await open_game(slack, "U1", "U2")
    await slack.command("U2", "end")
    assert "Only the host" in slack.last_response("U2")
    assert (await slack.h.game()).state is GameState.LOBBY


async def _state(slack: SlackDriver, state: GameState) -> bool:
    return (await slack.h.game()).state is state


async def _status(slack: SlackDriver, user: str, status: PlayerStatus) -> bool:
    return (await slack.h.player(user)).status is status
