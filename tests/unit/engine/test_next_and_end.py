import asyncio
from datetime import timedelta

import pytest

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import TimerKind
from jargonator.slack import ids
from jargonator.slack.gateway import MessageRef
from tests.engine_harness import Harness


async def finish_round(harness: Harness, *users: str) -> None:
    """Play one full round (everyone guesses "kitties")."""
    if users:
        await harness.started(*users)
    await harness.write()
    rnd = await harness.round()
    for user in await harness.guessers():
        await harness.engine.submit_guess(user, rnd.id, "I own kitties")
    assert (await harness.game()).state is GameState.AWAITING_NEXT


async def test_next_requires_host(harness: Harness) -> None:
    await finish_round(harness, "U1", "U2", "U3")
    with pytest.raises(UserFacingError, match="Only the host"):
        await harness.engine.next_round("C1", "U2")


async def test_next_starts_round_two_and_clears_buttons(harness: Harness) -> None:
    await finish_round(harness, "U1", "U2", "U3")
    first = await harness.round()
    await harness.engine.next_round("C1", "U1")
    second = await harness.round()
    assert second.number == 2 and second.writer_user_id != first.writer_user_id
    assert (await harness.game()).state is GameState.AWAITING_SENTENCE
    assert first.results_message_ts is not None
    old = harness.slack.current(MessageRef("C1", first.results_message_ts))
    assert ids.NEXT not in str(old.blocks)
    assert harness.recorder.when((await harness.game()).id, TimerKind.HOST_CLAIM) is None


async def test_next_when_not_waiting(harness: Harness) -> None:
    await harness.started("U1", "U2")
    with pytest.raises(UserFacingError, match="no round to move on"):
        await harness.engine.next_round("C1", "U1")


async def test_pause_when_players_drop_then_resume(harness: Harness) -> None:
    await finish_round(harness, "U1", "U2")
    await harness.engine.leave("C1", "U2")
    await harness.engine.next_round("C1", "U1")
    assert (await harness.game()).state is GameState.PAUSED_PLAYERS
    assert any("Not enough players" in t for t in harness.channel_texts())
    with pytest.raises(UserFacingError, match="Still waiting"):
        await harness.engine.next_round("C1", "U1")
    await harness.engine.join("C1", "U3")
    await harness.engine.next_round("C1", "U1")
    assert (await harness.game()).state is GameState.AWAITING_SENTENCE
    assert (await harness.round()).number == 2


async def test_end_mid_guessing(harness: Harness) -> None:
    await harness.started("U1", "U2", "U3")
    await harness.write()
    rnd = await harness.round()
    guessers = await harness.guessers()
    await harness.engine.submit_guess(guessers[0], rnd.id, "I own kitties")
    await harness.engine.end_game("C1", "U1")

    game = await harness.game()
    assert game.state is GameState.ENDED and game.end_reason == "host"
    assert (await harness.round()).status is RoundStatus.VOIDED
    assert all(p.score == 0 for p in await harness.players())
    assert any("ended" in m.text for m in harness.slack.dms_to(guessers[1]))
    assert any("Final scoreboard" in t for t in harness.channel_texts())
    assert "action_id" not in str((await harness.lobby_message()).blocks)
    assert not [k for k in harness.recorder.scheduled if k[0] == game.id]
    # the channel and the players are free again
    await harness.create("U2")
    assert (await harness.game()).host_user_id == "U2"


async def test_end_by_admin_and_non_admin(harness: Harness) -> None:
    harness.slack.admins.add("UADMIN")
    await harness.lobby_with("U1", "U2")
    with pytest.raises(UserFacingError, match="host"):
        await harness.engine.end_game("C1", "U2")
    await harness.engine.end_game("C1", "UADMIN")
    assert (await harness.game()).end_reason == "admin"


async def test_final_scoreboard_contents(harness: Harness) -> None:
    harness.llm.judge_scores = {"I own kitties": 97}
    await finish_round(harness, "U1", "U2", "U3")
    await harness.engine.end_game("C1", "U1")
    final = [m for m in harness.slack.messages_in("C1") if "Final scoreboard" in m.text][-1]
    assert "I own kitties" in str(final.blocks) and "1 round" in str(final.blocks)


async def test_idle_timeout_ends_game(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2")
    harness.clock.advance(7200)
    await harness.engine.on_idle_timeout(game.id)
    game = await harness.game()
    assert game.state is GameState.ENDED and game.end_reason == "idle"


async def test_activity_pushes_idle_out(harness: Harness) -> None:
    game = await harness.create("U1")
    harness.clock.advance(3600)
    await harness.engine.join("C1", "U2")
    harness.clock.advance(3600)
    await harness.engine.on_idle_timeout(game.id)  # stale timer: only 1 h since activity
    assert (await harness.game()).state is GameState.LOBBY
    assert harness.recorder.when(game.id, TimerKind.IDLE) == harness.clock.now() + timedelta(
        hours=1
    )


async def test_llm_failure_end_posts_scoreboard_with_scores_so_far(harness: Harness) -> None:
    harness.llm.judge_scores = {"I own kitties": 90}
    await finish_round(harness, "U1", "U2", "U3")
    await harness.engine.next_round("C1", "U1")
    from jargonator.llm.client import LLMError

    harness.llm.fail("generate_jargon", LLMError("down"), times=2)
    await harness.write()
    await harness.engine.on_llm_retry((await harness.game()).id, (await harness.round()).id)
    texts = harness.channel_texts()
    failure = next(i for i, t in enumerate(texts) if "can't continue" in t)
    board = next(i for i, t in enumerate(texts) if "Final scoreboard" in t)
    assert failure < board
    assert (await harness.game()).end_reason == "llm_failure"
    assert max(p.score for p in await harness.players()) == 10


async def test_judge_finishing_after_end_is_discarded(harness: Harness) -> None:
    entered, release = harness.llm.hold("judge")
    await harness.started("U1", "U2")
    await harness.write()
    rnd = await harness.round()
    guesser = (await harness.guessers())[0]
    judging = asyncio.create_task(harness.engine.submit_guess(guesser, rnd.id, "I own kitties"))
    await asyncio.wait_for(entered.wait(), 3)
    await harness.engine.end_game("C1", "U1")
    release.set()
    await judging
    assert all(p.score == 0 for p in await harness.players())
    assert (await harness.round()).status is RoundStatus.VOIDED
    assert not any("Round 1 results" in t for t in harness.channel_texts())
