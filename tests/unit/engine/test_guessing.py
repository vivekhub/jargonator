import asyncio

import pytest

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import TimerKind
from jargonator.slack import ids
from jargonator.slack.gateway import MessageRef
from tests.engine_harness import Harness, eventually


async def guessing(harness: Harness, *users: str) -> tuple[str, list[str], str]:
    """Start a game and get to GUESSING. Returns (writer, guessers, round_id)."""
    rnd = await harness.started(*users)
    await harness.write()
    return rnd.writer_user_id, sorted(await harness.guessers()), rnd.id


async def status_message(harness: Harness) -> MessageRef:
    rnd = await harness.round()
    assert rnd.status_message_ts is not None
    return MessageRef("C1", rnd.status_message_ts)


async def test_validation_errors(harness: Harness) -> None:
    writer, guessers, round_id = await guessing(harness, "U1", "U2", "U3")
    await harness.engine.join("C1", "U4")  # late joiner
    with pytest.raises(UserFacingError, match="You wrote this one"):
        await harness.engine.submit_guess(writer, round_id, "anything")
    with pytest.raises(UserFacingError, match="next one"):
        await harness.engine.submit_guess("U4", round_id, "anything")
    with pytest.raises(UserFacingError, match="1–200"):
        await harness.engine.submit_guess(guessers[0], round_id, "   ")
    with pytest.raises(UserFacingError, match="1–200"):
        await harness.engine.submit_guess(guessers[0], round_id, "x" * 201)
    with pytest.raises(UserFacingError, match="over"):
        await harness.engine.submit_guess(guessers[0], "nope", "cats")
    await harness.engine.submit_guess(guessers[0], round_id, "I own cats")
    with pytest.raises(UserFacingError, match="You already guessed: I own cats"):
        await harness.engine.submit_guess(guessers[0], round_id, "dogs")
    harness.clock.advance(61)
    with pytest.raises(UserFacingError, match="Time's up"):
        await harness.engine.submit_guess(guessers[1], round_id, "late")


async def test_guess_updates_the_guessers_dm(harness: Harness) -> None:
    _, guessers, round_id = await guessing(harness, "U1", "U2", "U3")
    await harness.engine.submit_guess(guessers[0], round_id, "I own cats")
    prompt = harness.slack.dms_to(guessers[0])[-1]
    assert "Your guess:* I own cats" in str(prompt.blocks)
    assert ids.SUBMIT_GUESS not in str(prompt.blocks)
    guesses = await harness.repo.get_guesses(round_id)
    assert [(g.user_id, g.text) for g in guesses] == [(guessers[0], "I own cats")]


async def test_counter_updates_are_debounced(harness: Harness) -> None:
    users = [f"U{i}" for i in range(1, 13)]  # 1 writer + 11 guessers
    _, guessers, round_id = await guessing(harness, *users)
    ref = await status_message(harness)
    before = len(harness.slack.current(ref).history)
    for user in guessers[:10]:
        await harness.engine.submit_guess(user, round_id, f"guess from {user}")
    await harness.clock.settle()
    updates_within_first_second = len(harness.slack.current(ref).history) - before
    assert updates_within_first_second <= 2
    await harness.clock.tick(1)
    await eventually(lambda: "10/11 guessed" in harness.slack.current(ref).text)
    assert len(harness.slack.current(ref).history) - before <= 2


async def test_last_guess_closes_early(harness: Harness) -> None:
    _, guessers, round_id = await guessing(harness, "U1", "U2", "U3")
    game_id = (await harness.game()).id
    for user in guessers:
        await harness.engine.submit_guess(user, round_id, "I have cats")
    assert (await harness.round()).status is not RoundStatus.GUESSING
    assert (await harness.game()).state is not GameState.GUESSING
    assert harness.recorder.when(game_id, TimerKind.GUESS_DEADLINE) is None
    # a late deadline timer is a no-op
    await harness.engine.on_guess_deadline(game_id, round_id)


async def test_deadline_closes_guessing(harness: Harness) -> None:
    _, guessers, round_id = await guessing(harness, "U1", "U2", "U3")
    await harness.engine.submit_guess(guessers[0], round_id, "I have cats")
    harness.clock.advance(60)
    await harness.engine.on_guess_deadline((await harness.game()).id, round_id)
    assert (await harness.round()).status is not RoundStatus.GUESSING
    status = harness.slack.current(await status_message(harness))
    assert any("guessing closed" in text for text, _ in status.history)
    assert "Results below" in status.text  # judging ran straight after closing


async def test_last_pending_guesser_leaving_closes_early(harness: Harness) -> None:
    _, guessers, round_id = await guessing(harness, "U1", "U2", "U3")
    await harness.engine.submit_guess(guessers[0], round_id, "I have cats")
    await harness.engine.leave("C1", guessers[1])
    assert (await harness.round()).status is not RoundStatus.GUESSING


async def test_guesser_who_left_after_guessing_still_counts(harness: Harness) -> None:
    _, guessers, round_id = await guessing(harness, "U1", "U2", "U3", "U4")
    await harness.engine.submit_guess(guessers[0], round_id, "I have cats")
    await harness.engine.leave("C1", guessers[0])
    assert (await harness.round()).status is RoundStatus.GUESSING  # others still pending
    assert len(await harness.repo.get_guesses(round_id)) == 1


async def test_race_between_guesses_and_deadline(harness: Harness) -> None:
    users = ["U1", "U2", "U3", "U4", "U5"]
    _, guessers, round_id = await guessing(harness, *users)
    game_id = (await harness.game()).id
    results = await asyncio.gather(
        *(harness.engine.submit_guess(u, round_id, f"guess {u}") for u in guessers[:3]),
        harness.engine.on_guess_deadline(game_id, round_id),
        return_exceptions=True,
    )
    accepted = [r for r in results[:3] if not isinstance(r, Exception)]
    rejected = [r for r in results[:3] if isinstance(r, Exception)]
    assert all(isinstance(r, UserFacingError) for r in rejected)
    guesses = await harness.repo.get_guesses(round_id)
    assert len(guesses) == len(accepted)
    assert len({g.user_id for g in guesses}) == len(guesses)
    assert (await harness.round()).status is not RoundStatus.GUESSING
    closes = [m for m in harness.slack.messages_in("C1") if "guessing closed" in m.text]
    assert len(closes) <= 1
