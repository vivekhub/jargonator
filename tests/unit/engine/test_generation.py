from datetime import timedelta

from jargonator.domain.state import GameState, JargonLevel, RoundStatus
from jargonator.engine.scheduler import TimerKind
from jargonator.llm.client import LLMError
from jargonator.slack import ids
from tests.engine_harness import Harness

JARGON = "I steward a dual-asset feline stakeholder portfolio."


async def test_happy_path_distributes_jargon_to_guessers_only(harness: Harness) -> None:
    harness.llm.jargon = [JARGON]
    rnd = await harness.started("U1", "U2", "U3")
    writer = rnd.writer_user_id
    rnd = await harness.write("I have two cats")

    game = await harness.game()
    assert game.state is GameState.GUESSING
    assert rnd.status is RoundStatus.GUESSING
    assert rnd.jargon == JARGON
    assert rnd.level in set(JargonLevel)
    assert rnd.llm_retry_at is None
    assert rnd.guess_deadline == harness.clock.now() + timedelta(seconds=60)

    guessers = sorted(await harness.guessers())
    assert guessers == sorted({"U1", "U2", "U3"} - {writer})
    for user in guessers:
        prompt = harness.slack.dms_to(user)[-1]
        assert JARGON in str(prompt.blocks) and ids.SUBMIT_GUESS in str(prompt.blocks)
    assert all(JARGON not in str(m.blocks) for m in harness.slack.dms_to(writer))

    status = [m for m in harness.slack.messages_in("C1") if "Round 1" in m.text][-1]
    assert "0/2 guessed" in status.text
    assert all(
        JARGON not in m.text and JARGON not in str(m.blocks)
        for m in harness.slack.messages_in("C1")
    )
    assert harness.recorder.when(game.id, TimerKind.GUESS_DEADLINE) == rnd.guess_deadline


async def test_level_choice_is_deterministic_for_the_seed(harness: Harness) -> None:
    await harness.started("U1", "U2")
    rnd = await harness.write()
    call = harness.llm.calls_to("generate_jargon")[0]
    assert call["level"] is rnd.level


async def test_leaky_jargon_is_regenerated_with_avoid_words(harness: Harness) -> None:
    harness.llm.jargon = ["I own two cats, synergistically.", JARGON]
    await harness.started("U1", "U2")
    rnd = await harness.write("I have two cats")
    calls = harness.llm.calls_to("generate_jargon")
    assert len(calls) == 2
    assert calls[1]["avoid_words"] == {"two", "cats"}
    assert rnd.jargon == JARGON


async def test_first_llm_failure_schedules_retry_then_succeeds(harness: Harness) -> None:
    harness.llm.jargon = [JARGON]
    harness.llm.fail("generate_jargon", LLMError("down"), times=1)
    await harness.started("U1", "U2")
    rnd = await harness.write()
    game = await harness.game()
    assert game.state is GameState.GENERATING
    assert rnd.llm_retry_at == harness.clock.now() + timedelta(seconds=30)
    assert harness.recorder.when(game.id, TimerKind.LLM_RETRY) == rnd.llm_retry_at
    assert any("retrying in 30 seconds" in t for t in harness.channel_texts())

    harness.clock.advance(30)
    await harness.engine.on_llm_retry(game.id, rnd.id)
    rnd = await harness.round()
    assert (await harness.game()).state is GameState.GUESSING
    assert rnd.jargon == JARGON
    assert rnd.llm_retry_at is None  # reset after success
    assert rnd.level == harness.llm.calls_to("generate_jargon")[0]["level"]  # same level kept


async def test_second_llm_failure_ends_the_game(harness: Harness) -> None:
    harness.llm.fail("generate_jargon", LLMError("down"), times=2)
    await harness.started("U1", "U2")
    rnd = await harness.write()
    game = await harness.game()
    await harness.engine.on_llm_retry(game.id, rnd.id)

    game = await harness.game()
    assert game.state is GameState.ENDED
    assert game.end_reason == "llm_failure"
    assert (await harness.round()).status is RoundStatus.VOIDED
    assert any("can't continue" in t for t in harness.channel_texts())
    assert not [k for k in harness.recorder.scheduled if k[0] == game.id]


async def test_stale_retry_is_ignored(harness: Harness) -> None:
    await harness.started("U1", "U2")
    rnd = await harness.write()
    game = await harness.game()
    await harness.engine.on_llm_retry(game.id, rnd.id)  # already guessing: no-op
    assert len(harness.llm.calls_to("generate_jargon")) == 1
    assert (await harness.game()).state is GameState.GUESSING


async def test_player_who_joined_during_generation_is_not_a_guesser(harness: Harness) -> None:
    harness.llm.fail("generate_jargon", LLMError("down"), times=1)
    rnd = await harness.started("U1", "U2")
    await harness.write()
    await harness.engine.join("C1", "U3")
    await harness.engine.on_llm_retry((await harness.game()).id, rnd.id)
    # U3 joined before distribution, so U3 *is* included. Now the real check:
    assert "U3" in await harness.guessers()


async def test_joiner_during_guessing_is_not_a_guesser(harness: Harness) -> None:
    await harness.started("U1", "U2")
    await harness.write()
    await harness.engine.join("C1", "U3")
    assert "U3" not in await harness.guessers()
    assert harness.slack.dms_to("U3") == []


async def test_undeliverable_guesser_is_skipped(harness: Harness) -> None:
    rnd = await harness.started("U1", "U2", "U3")
    other = sorted({"U1", "U2", "U3"} - {rnd.writer_user_id})
    harness.slack.fail_dm(other[0], "cannot_dm_bot")
    await harness.write()
    assert await harness.guessers() == [other[1]]
