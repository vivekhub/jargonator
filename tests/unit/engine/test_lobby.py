import asyncio

import pytest

from jargonator.domain.state import GameState, PlayerStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import TimerKind
from jargonator.slack import ids
from tests.engine_harness import T0, Harness


async def test_create_game_posts_lobby_and_schedules_timers(harness: Harness) -> None:
    game = await harness.create("U1", join_window=120)
    assert game.state is GameState.LOBBY
    lobby = await harness.lobby_message()
    assert "👑 <@U1>" in str(lobby.blocks)
    assert ids.START in str(lobby.blocks)
    assert harness.recorder.when(game.id, TimerKind.LOBBY) == T0.replace(minute=2)
    assert harness.recorder.when(game.id, TimerKind.IDLE) is not None
    assert [p.user_id for p in await harness.players()] == ["U1"]


async def test_no_lobby_timer_with_zero_join_window(harness: Harness) -> None:
    game = await harness.create("U1", join_window=0)
    assert harness.recorder.when(game.id, TimerKind.LOBBY) is None


async def test_second_game_in_channel_refused(harness: Harness) -> None:
    await harness.create("U1")
    with pytest.raises(UserFacingError, match="already a game"):
        await harness.create("U2")


async def test_user_in_another_game_refused(harness: Harness) -> None:
    await harness.create("U1")
    harness.channel = "C2"
    with pytest.raises(UserFacingError, match="<#C1>"):
        await harness.create("U1")


async def test_create_in_channel_without_bot_fails_cleanly(harness: Harness) -> None:
    harness.slack.fail_channel("C1", "not_in_channel")
    with pytest.raises(UserFacingError, match="/invite"):
        await harness.create("U1")
    assert await harness.repo.get_active_game_by_channel("C1") is None  # channel stays free
    assert await harness.repo.find_active_game_for_user("U1") is None


async def test_join_updates_lobby(harness: Harness) -> None:
    await harness.create("U1")
    await harness.engine.join("C1", "U2")
    assert "<@U2>" in str((await harness.lobby_message()).blocks)
    assert [p.user_id for p in await harness.players()] == ["U1", "U2"]


async def test_duplicate_join_is_idempotent(harness: Harness) -> None:
    await harness.create("U1")
    await harness.engine.join("C1", "U2")
    with pytest.raises(UserFacingError, match="already in this game"):
        await harness.engine.join("C1", "U2")
    assert len(await harness.players()) == 2


async def test_join_without_game(harness: Harness) -> None:
    with pytest.raises(UserFacingError, match="No game"):
        await harness.engine.join("C1", "U2")


async def test_join_while_in_other_game_refused(harness: Harness) -> None:
    await harness.create("U1")
    harness.channel = "C2"
    await harness.create("U2")
    with pytest.raises(UserFacingError, match="<#C2>"):
        await harness.engine.join("C1", "U2")


async def test_leave_and_rejoin_keeps_score(harness: Harness) -> None:
    game = await harness.lobby_with("U1", "U2")
    await harness.repo.add_points(game.id, "U2", 15, round_win=True)
    await harness.engine.leave("C1", "U2")
    assert (await harness.player("U2")).status is PlayerStatus.LEFT
    assert "<@U2> — left" in str((await harness.lobby_message()).blocks)
    await harness.engine.join("C1", "U2")
    rejoined = await harness.player("U2")
    assert (rejoined.status, rejoined.score) == (PlayerStatus.ACTIVE, 15)


async def test_leave_when_not_in_game(harness: Harness) -> None:
    await harness.create("U1")
    with pytest.raises(UserFacingError, match="not in this game"):
        await harness.engine.leave("C1", "U9")


async def test_host_leaving_transfers_host(harness: Harness) -> None:
    await harness.lobby_with("U1", "U2", "U3")
    await harness.engine.leave("C1", "U1")
    assert (await harness.game()).host_user_id == "U2"
    assert any("👑 <@U2> is now the host" in t for t in harness.channel_texts())


async def test_last_player_leaving_keeps_host(harness: Harness) -> None:
    await harness.create("U1")
    await harness.engine.leave("C1", "U1")
    assert (await harness.game()).host_user_id == "U1"


async def test_actions_bump_idle_deadline(harness: Harness) -> None:
    game = await harness.create("U1")
    harness.clock.advance(600)
    await harness.engine.join("C1", "U2")
    refreshed = await harness.game()
    assert refreshed.last_activity_at == harness.clock.now()
    assert harness.recorder.when(game.id, TimerKind.IDLE) == refreshed.idle_deadline
    assert refreshed.idle_deadline is not None
    assert (refreshed.idle_deadline - harness.clock.now()).total_seconds() == 7200


async def test_concurrent_joins_persist_once(harness: Harness) -> None:
    await harness.create("U1")
    users = [f"U{i}" for i in range(2, 7)]
    await asyncio.gather(*(harness.engine.join("C1", u) for u in users))
    assert sorted(p.user_id for p in await harness.players()) == ["U1", *users]
    lobby = await harness.lobby_message()
    assert all(f"<@{u}>" in str(lobby.blocks) for u in users)
