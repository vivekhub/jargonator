import asyncio
from datetime import timedelta

import pytest

from jargonator.domain.state import GameState, PlayerStatus, RoundStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import TimerKind
from jargonator.slack import ids
from tests.engine_harness import T0, Harness


async def start(harness: Harness, *users: str) -> None:
    await harness.lobby_with(*users)
    await harness.engine.start_game("C1", users[0])


async def test_start_needs_two_players(harness: Harness) -> None:
    await harness.create("U1")
    with pytest.raises(UserFacingError, match="at least 2 players"):
        await harness.engine.start_game("C1", "U1")


async def test_only_host_can_start(harness: Harness) -> None:
    await harness.lobby_with("U1", "U2")
    with pytest.raises(UserFacingError, match=r"Only the host \(<@U1>\)"):
        await harness.engine.start_game("C1", "U2")


async def test_start_begins_round_one(harness: Harness) -> None:
    game = await harness.create("U1", join_window=120)
    await harness.engine.join("C1", "U2")
    await harness.engine.start_game("C1", "U1")

    game = await harness.game()
    assert game.state is GameState.AWAITING_SENTENCE
    assert game.started_at == T0
    rnd = await harness.round()
    assert rnd.number == 1 and rnd.status is RoundStatus.AWAITING_SENTENCE
    assert rnd.writer_deadline == T0 + timedelta(seconds=90)
    assert rnd.writer_reminder_at == T0 + timedelta(seconds=60)

    writer = rnd.writer_user_id
    other = ({"U1", "U2"} - {writer}).pop()
    assert any("Round 1" in t and f"<@{writer}>" in t for t in harness.channel_texts())
    assert len(harness.slack.dms_to(writer)) == 1
    assert ids.WRITE_SENTENCE in str(harness.slack.dms_to(writer)[0].blocks)
    assert harness.slack.dms_to(other) == []

    assert harness.recorder.when(game.id, TimerKind.WRITER_DEADLINE) == rnd.writer_deadline
    assert harness.recorder.when(game.id, TimerKind.WRITER_REMINDER) == rnd.writer_reminder_at
    assert harness.recorder.when(game.id, TimerKind.LOBBY) is None  # cancelled
    assert ids.START not in str((await harness.lobby_message()).blocks)
    assert await harness.repo.load_turn_order(game.id) is not None


async def test_start_twice_refused(harness: Harness) -> None:
    await start(harness, "U1", "U2")
    with pytest.raises(UserFacingError, match="already started"):
        await harness.engine.start_game("C1", "U1")


async def test_no_reminder_when_writer_time_is_short(harness: Harness) -> None:
    await harness.create("U1", writer=30)
    await harness.engine.join("C1", "U2")
    await harness.engine.start_game("C1", "U1")
    rnd = await harness.round()
    assert rnd.writer_reminder_at is None
    assert harness.recorder.when((await harness.game()).id, TimerKind.WRITER_REMINDER) is None


async def test_lobby_deadline_starts_with_two_players(harness: Harness) -> None:
    game = await harness.create("U1", join_window=120)
    await harness.engine.join("C1", "U2")
    await harness.engine.on_lobby_deadline(game.id)
    assert (await harness.game()).state is GameState.AWAITING_SENTENCE


async def test_lobby_deadline_with_one_player_keeps_lobby(harness: Harness) -> None:
    game = await harness.create("U1", join_window=120)
    await harness.engine.on_lobby_deadline(game.id)
    assert (await harness.game()).state is GameState.LOBBY
    assert ids.START in str((await harness.lobby_message()).blocks)


# --- sentence submission -----------------------------------------------------------------


async def writer_and_round(harness: Harness) -> tuple[str, str]:
    rnd = await harness.round()
    return rnd.writer_user_id, rnd.id


async def test_sentence_validation(harness: Harness) -> None:
    await start(harness, "U1", "U2")
    writer, round_id = await writer_and_round(harness)
    other = ({"U1", "U2"} - {writer}).pop()
    with pytest.raises(UserFacingError, match="not your turn"):
        await harness.engine.submit_sentence(other, round_id, "I have two cats")
    with pytest.raises(UserFacingError, match="5"):
        await harness.engine.submit_sentence(writer, round_id, " hi ")
    with pytest.raises(UserFacingError, match="150"):
        await harness.engine.submit_sentence(writer, round_id, "x" * 151)
    with pytest.raises(UserFacingError, match="over"):
        await harness.engine.submit_sentence(writer, "no-such-round", "I have two cats")
    harness.clock.advance(91)
    with pytest.raises(UserFacingError, match="Time's up"):
        await harness.engine.submit_sentence(writer, round_id, "I have two cats")
    assert harness.llm.calls_to("moderate_sentence") == []


async def test_rejected_sentence_keeps_waiting(harness: Harness) -> None:
    harness.llm.rejection = "Please keep it about you."
    await start(harness, "U1", "U2")
    writer, round_id = await writer_and_round(harness)
    await harness.engine.submit_sentence(writer, round_id, "Bob from sales is lazy")
    assert (await harness.game()).state is GameState.AWAITING_SENTENCE
    assert (await harness.round()).sentence is None
    last_dm = harness.slack.dms_to(writer)[-1]
    assert "Please keep it about you." in last_dm.text and ids.TRY_AGAIN in str(last_dm.blocks)
    game_id = (await harness.game()).id
    assert harness.recorder.when(game_id, TimerKind.WRITER_DEADLINE) is not None  # still running

    harness.llm.rejection = None
    await harness.engine.submit_sentence(writer, round_id, "I have two cats")
    rnd = await harness.round()
    assert rnd.sentence == "I have two cats"


async def test_accepted_sentence_moves_on(harness: Harness) -> None:
    await start(harness, "U1", "U2")
    writer, round_id = await writer_and_round(harness)
    game_id = (await harness.game()).id
    await harness.repo.update_player(game_id, writer, consecutive_misses=2)
    await harness.engine.submit_sentence(writer, round_id, "  I have two cats  ")
    rnd = await harness.round()
    assert rnd.sentence == "I have two cats"
    assert rnd.status is not RoundStatus.AWAITING_SENTENCE
    assert (await harness.game()).state is not GameState.AWAITING_SENTENCE
    assert (await harness.player(writer)).consecutive_misses == 0
    assert harness.recorder.when(game_id, TimerKind.WRITER_DEADLINE) is None
    assert harness.recorder.when(game_id, TimerKind.WRITER_REMINDER) is None
    assert any("Got it" in m.text for m in harness.slack.dms_to(writer))


async def test_duplicate_valid_submit_is_noop(harness: Harness) -> None:
    await start(harness, "U1", "U2")
    writer, round_id = await writer_and_round(harness)
    await asyncio.gather(
        harness.engine.submit_sentence(writer, round_id, "I have two cats"),
        harness.engine.submit_sentence(writer, round_id, "I have three dogs"),
    )
    rnd = await harness.round()
    assert rnd.sentence in {"I have two cats", "I have three dogs"}
    assert sum("Got it" in m.text for m in harness.slack.dms_to(writer)) == 1


async def test_inactive_player_not_chosen_as_writer(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2", "U3")
    await harness.repo.update_player(game.id, "U3", status=PlayerStatus.INACTIVE)
    await harness.engine.start_game("C1", "U1")
    assert (await harness.round()).writer_user_id in {"U1", "U2"}
