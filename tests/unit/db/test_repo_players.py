import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from jargonator.db.repo import Repo
from jargonator.domain.state import GameState, PlayerStatus

NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


async def new_game(repo: Repo, channel: str = "C1") -> str:
    game = await repo.create_game(
        channel_id=channel,
        host_user_id="U1",
        guess_seconds=60,
        writer_seconds=90,
        join_window_seconds=120,
        now=NOW,
    )
    return game.id


async def test_add_player(repo: Repo) -> None:
    game_id = await new_game(repo)
    player = await repo.add_or_reactivate_player(game_id, "U1", NOW)
    assert player.user_id == "U1"
    assert player.status is PlayerStatus.ACTIVE
    assert (player.score, player.round_wins, player.consecutive_misses) == (0, 0, 0)
    assert player.joined_at == NOW


async def test_add_is_idempotent(repo: Repo) -> None:
    game_id = await new_game(repo)
    await repo.add_or_reactivate_player(game_id, "U1", NOW)
    await repo.add_or_reactivate_player(game_id, "U1", NOW + timedelta(seconds=5))
    assert len(await repo.get_players(game_id)) == 1


async def test_rejoin_keeps_score_and_resets_misses(repo: Repo) -> None:
    game_id = await new_game(repo)
    await repo.add_or_reactivate_player(game_id, "U1", NOW)
    await repo.add_points(game_id, "U1", 15, round_win=True)
    later = NOW + timedelta(minutes=5)
    await repo.update_player(
        game_id, "U1", status=PlayerStatus.LEFT, left_at=later, consecutive_misses=2
    )
    rejoined = await repo.add_or_reactivate_player(game_id, "U1", later + timedelta(minutes=1))
    assert rejoined.status is PlayerStatus.ACTIVE
    assert (rejoined.score, rejoined.round_wins, rejoined.consecutive_misses) == (15, 1, 0)
    assert rejoined.left_at is None


async def test_add_points_accumulates(repo: Repo) -> None:
    game_id = await new_game(repo)
    await repo.add_or_reactivate_player(game_id, "U1", NOW)
    await repo.add_points(game_id, "U1", 10, round_win=True)
    await repo.add_points(game_id, "U1", 5, round_win=False)
    player = await repo.get_player(game_id, "U1")
    assert player is not None and (player.score, player.round_wins) == (15, 1)


async def test_get_players_in_join_order(repo: Repo) -> None:
    game_id = await new_game(repo)
    for i, user in enumerate(["U3", "U1", "U2"]):
        await repo.add_or_reactivate_player(game_id, user, NOW + timedelta(seconds=i))
    assert [p.user_id for p in await repo.get_players(game_id)] == ["U3", "U1", "U2"]


async def test_get_missing_player(repo: Repo) -> None:
    game_id = await new_game(repo)
    assert await repo.get_player(game_id, "U9") is None


async def test_update_player_rejects_unknown_fields(repo: Repo) -> None:
    game_id = await new_game(repo)
    await repo.add_or_reactivate_player(game_id, "U1", NOW)
    with pytest.raises(ValueError, match="score"):
        await repo.update_player(game_id, "U1", score=100)


async def test_find_active_game_for_user_across_channels(repo: Repo) -> None:
    game_c1 = await new_game(repo, "C1")
    await new_game(repo, "C2")
    await repo.add_or_reactivate_player(game_c1, "U7", NOW)
    found = await repo.find_active_game_for_user("U7")
    assert found is not None and found.channel_id == "C1"
    assert await repo.find_active_game_for_user("U8") is None


async def test_find_active_game_ignores_left_players_and_ended_games(repo: Repo) -> None:
    g1 = await new_game(repo, "C1")
    await repo.add_or_reactivate_player(g1, "U7", NOW)
    await repo.update_player(g1, "U7", status=PlayerStatus.INACTIVE)
    assert await repo.find_active_game_for_user("U7") is not None  # inactive still counts
    await repo.update_player(g1, "U7", status=PlayerStatus.LEFT)
    assert await repo.find_active_game_for_user("U7") is None
    await repo.add_or_reactivate_player(g1, "U7", NOW)
    await repo.update_game(g1, state=GameState.ENDED)
    assert await repo.find_active_game_for_user("U7") is None


async def test_concurrent_adds_persist_once(repo: Repo) -> None:
    game_id = await new_game(repo)
    users = [f"U{i}" for i in range(5)]
    await asyncio.gather(*(repo.add_or_reactivate_player(game_id, u, NOW) for u in users * 2))
    assert sorted(p.user_id for p in await repo.get_players(game_id)) == users


async def test_foreign_keys_enforced(repo: Repo) -> None:
    with pytest.raises(Exception, match="FOREIGN KEY"):
        await repo.add_or_reactivate_player("no-such-game", "U1", NOW)
