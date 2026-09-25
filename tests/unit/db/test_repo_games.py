from datetime import UTC, datetime, timedelta, timezone

import pytest

from jargonator.db.repo import ChannelBusyError, Repo, UserInOtherGameError
from jargonator.domain.state import GameState, PlayerStatus

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


async def new_game(repo: Repo, channel: str = "C1", host: str = "U1") -> str:
    game = await repo.create_game(
        channel_id=channel,
        host_user_id=host,
        guess_seconds=60,
        writer_seconds=90,
        join_window_seconds=120,
        lobby_deadline=NOW + timedelta(seconds=120),
        idle_deadline=NOW + timedelta(hours=2),
        now=NOW,
    )
    return game.id


async def test_create_and_get_game(repo: Repo) -> None:
    game_id = await new_game(repo)
    game = await repo.get_game(game_id)
    assert game is not None
    assert game.channel_id == "C1"
    assert game.host_user_id == "U1"
    assert game.created_by == "U1"
    assert game.state is GameState.LOBBY
    assert (game.guess_seconds, game.writer_seconds, game.join_window_seconds) == (60, 90, 120)
    assert game.created_at == NOW
    assert game.last_activity_at == NOW
    assert game.started_at is None


async def test_get_missing_game(repo: Repo) -> None:
    assert await repo.get_game("nope") is None


async def test_second_active_game_in_channel_is_refused(repo: Repo) -> None:
    await new_game(repo, "C1")
    with pytest.raises(ChannelBusyError):
        await new_game(repo, "C1", host="U2")
    await new_game(repo, "C2", host="U3")  # other channels are fine


async def test_new_game_allowed_after_previous_ended(repo: Repo) -> None:
    first = await new_game(repo, "C1")
    await repo.update_game(first, state=GameState.ENDED, ended_at=NOW, end_reason="host")
    second = await new_game(repo, "C1")
    active = await repo.get_active_game_by_channel("C1")
    assert active is not None and active.id == second


async def test_get_active_game_by_channel(repo: Repo) -> None:
    assert await repo.get_active_game_by_channel("C1") is None
    game_id = await new_game(repo, "C1")
    active = await repo.get_active_game_by_channel("C1")
    assert active is not None and active.id == game_id


async def test_timestamps_round_trip_as_utc(repo: Repo) -> None:
    game_id = await new_game(repo)
    ist = timezone(timedelta(hours=5, minutes=30))
    local = datetime(2026, 3, 1, 15, 0, tzinfo=ist)
    updated = await repo.update_game(game_id, lobby_deadline=local)
    assert updated.lobby_deadline == local
    assert updated.lobby_deadline is not None
    assert updated.lobby_deadline.tzinfo == UTC
    reloaded = await repo.get_game(game_id)
    assert reloaded is not None and reloaded.lobby_deadline == datetime(
        2026, 3, 1, 9, 30, tzinfo=UTC
    )


async def test_naive_datetimes_rejected(repo: Repo) -> None:
    game_id = await new_game(repo)
    with pytest.raises(Exception, match="timezone-aware"):
        await repo.update_game(game_id, lobby_deadline=datetime(2026, 3, 1, 9, 30))


async def test_update_game_rejects_unknown_fields(repo: Repo) -> None:
    game_id = await new_game(repo)
    with pytest.raises(ValueError, match="channel_id"):
        await repo.update_game(game_id, channel_id="C9")


async def test_update_missing_game_raises(repo: Repo) -> None:
    with pytest.raises(LookupError):
        await repo.update_game("nope", state=GameState.ENDED)


async def test_list_non_ended_games(repo: Repo) -> None:
    a = await new_game(repo, "C1")
    b = await new_game(repo, "C2", host="U2")
    await repo.update_game(a, state=GameState.ENDED)
    assert [g.id for g in await repo.list_non_ended_games()] == [b]


async def test_create_game_adds_host_and_deadlines_atomically(repo: Repo) -> None:
    """Review finding 4: no half-created game (row without host/deadlines)."""
    game_id = await new_game(repo, "C1", host="U5")
    players = await repo.get_players(game_id)
    assert [(p.user_id, p.status) for p in players] == [("U5", PlayerStatus.ACTIVE)]
    game = await repo.get_game(game_id)
    assert game is not None
    assert game.lobby_deadline == NOW + timedelta(seconds=120)
    assert game.idle_deadline == NOW + timedelta(hours=2)


async def test_create_game_refused_when_host_in_another_game(repo: Repo) -> None:
    await new_game(repo, "C1", host="U5")
    with pytest.raises(UserInOtherGameError):
        await new_game(repo, "C2", host="U5")
    assert await repo.get_active_game_by_channel("C2") is None  # nothing half-created
