"""Repository: the only code that talks to the database (spec.md §10).

Every public method runs in its own transaction and returns plain records.
"""

import uuid
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import case, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jargonator.db.models import Game, Player
from jargonator.db.records import GameRecord, PlayerRecord
from jargonator.domain.state import GameState, PlayerStatus


class ChannelBusyError(Exception):
    """The channel already has an active game (spec §3.2)."""


GAME_UPDATABLE = frozenset(
    {
        "host_user_id",
        "state",
        "lobby_message_ts",
        "started_at",
        "ended_at",
        "end_reason",
        "lobby_deadline",
        "idle_deadline",
        "last_activity_at",
    }
)
PLAYER_UPDATABLE = frozenset({"status", "consecutive_misses", "left_at", "joined_at"})


class RepoProtocol(Protocol):
    """What the engine depends on. Implemented by ``Repo``."""

    async def create_game(
        self,
        *,
        channel_id: str,
        host_user_id: str,
        guess_seconds: int,
        writer_seconds: int,
        join_window_seconds: int,
        now: datetime,
    ) -> GameRecord: ...

    async def get_game(self, game_id: str) -> GameRecord | None: ...

    async def get_active_game_by_channel(self, channel_id: str) -> GameRecord | None: ...

    async def update_game(self, game_id: str, **fields: Any) -> GameRecord: ...

    async def find_active_game_for_user(self, user_id: str) -> GameRecord | None: ...

    async def list_non_ended_games(self) -> list[GameRecord]: ...

    async def add_or_reactivate_player(
        self, game_id: str, user_id: str, now: datetime
    ) -> PlayerRecord: ...

    async def get_players(self, game_id: str) -> list[PlayerRecord]: ...

    async def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None: ...

    async def update_player(self, game_id: str, user_id: str, **fields: Any) -> PlayerRecord: ...

    async def add_points(
        self, game_id: str, user_id: str, points: int, *, round_win: bool
    ) -> PlayerRecord: ...


def _game_record(g: Game) -> GameRecord:
    return GameRecord(
        id=g.id,
        channel_id=g.channel_id,
        host_user_id=g.host_user_id,
        state=g.state,
        created_by=g.created_by,
        guess_seconds=g.guess_seconds,
        writer_seconds=g.writer_seconds,
        join_window_seconds=g.join_window_seconds,
        lobby_message_ts=g.lobby_message_ts,
        created_at=g.created_at,
        started_at=g.started_at,
        ended_at=g.ended_at,
        end_reason=g.end_reason,
        lobby_deadline=g.lobby_deadline,
        idle_deadline=g.idle_deadline,
        last_activity_at=g.last_activity_at,
    )


def _player_record(p: Player) -> PlayerRecord:
    return PlayerRecord(
        game_id=p.game_id,
        user_id=p.user_id,
        status=p.status,
        score=p.score,
        round_wins=p.round_wins,
        consecutive_misses=p.consecutive_misses,
        joined_at=p.joined_at,
        left_at=p.left_at,
    )


def _check_fields(fields: dict[str, Any], allowed: frozenset[str]) -> None:
    unknown = sorted(set(fields) - allowed)
    if unknown:
        raise ValueError(f"Fields not updatable: {', '.join(unknown)}")


class Repo:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    # --- games ----------------------------------------------------------------------

    async def create_game(
        self,
        *,
        channel_id: str,
        host_user_id: str,
        guess_seconds: int,
        writer_seconds: int,
        join_window_seconds: int,
        now: datetime,
    ) -> GameRecord:
        game = Game(
            id=str(uuid.uuid4()),
            channel_id=channel_id,
            host_user_id=host_user_id,
            state=GameState.LOBBY,
            created_by=host_user_id,
            guess_seconds=guess_seconds,
            writer_seconds=writer_seconds,
            join_window_seconds=join_window_seconds,
            created_at=now,
            last_activity_at=now,
        )
        try:
            async with self._sessionmaker.begin() as session:
                session.add(game)
                await session.flush()
                await session.refresh(game)
                return _game_record(game)
        except IntegrityError as exc:
            raise ChannelBusyError(channel_id) from exc

    async def get_game(self, game_id: str) -> GameRecord | None:
        async with self._sessionmaker() as session:
            game = await session.get(Game, game_id)
            return _game_record(game) if game else None

    async def get_active_game_by_channel(self, channel_id: str) -> GameRecord | None:
        async with self._sessionmaker() as session:
            game = await session.scalar(
                select(Game).where(Game.channel_id == channel_id, Game.state != GameState.ENDED)
            )
            return _game_record(game) if game else None

    async def update_game(self, game_id: str, **fields: Any) -> GameRecord:
        _check_fields(fields, GAME_UPDATABLE)
        async with self._sessionmaker.begin() as session:
            game = await session.get(Game, game_id)
            if game is None:
                raise LookupError(f"Game {game_id} not found")
            for name, value in fields.items():
                setattr(game, name, value)
            await session.flush()
            await session.refresh(game)  # reload so timestamps come back normalised to UTC
            return _game_record(game)

    async def find_active_game_for_user(self, user_id: str) -> GameRecord | None:
        async with self._sessionmaker() as session:
            game = await session.scalar(
                select(Game)
                .join(Player, Player.game_id == Game.id)
                .where(
                    Player.user_id == user_id,
                    Player.status.in_([PlayerStatus.ACTIVE, PlayerStatus.INACTIVE]),
                    Game.state != GameState.ENDED,
                )
                .limit(1)
            )
            return _game_record(game) if game else None

    async def list_non_ended_games(self) -> list[GameRecord]:
        async with self._sessionmaker() as session:
            games = await session.scalars(
                select(Game).where(Game.state != GameState.ENDED).order_by(Game.created_at)
            )
            return [_game_record(g) for g in games]

    # --- players ----------------------------------------------------------------------

    async def add_or_reactivate_player(
        self, game_id: str, user_id: str, now: datetime
    ) -> PlayerRecord:
        """Add a player, or reactivate one who left or went inactive.

        Score and round wins are kept. Misses reset. A player returning from ``left``
        gets a fresh ``joined_at``, so they don't count as longest-standing for host
        transfer (spec §3.10).
        """
        stmt = sqlite_insert(Player).values(
            game_id=game_id,
            user_id=user_id,
            status=PlayerStatus.ACTIVE,
            score=0,
            round_wins=0,
            consecutive_misses=0,
            joined_at=now,
        )
        stmt = stmt.on_conflict_do_update(
            index_elements=[Player.game_id, Player.user_id],
            set_={
                "joined_at": case(
                    (Player.status == PlayerStatus.LEFT.value, stmt.excluded.joined_at),
                    else_=Player.joined_at,
                ),
                "consecutive_misses": case(
                    (Player.status == PlayerStatus.ACTIVE.value, Player.consecutive_misses),
                    else_=0,
                ),
                "status": PlayerStatus.ACTIVE.value,
                "left_at": None,
            },
        )
        async with self._sessionmaker.begin() as session:
            await session.execute(stmt)
            return await self._require_player(session, game_id, user_id)

    async def get_players(self, game_id: str) -> list[PlayerRecord]:
        async with self._sessionmaker() as session:
            players = await session.scalars(
                select(Player)
                .where(Player.game_id == game_id)
                .order_by(Player.joined_at, Player.id)
            )
            return [_player_record(p) for p in players]

    async def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None:
        async with self._sessionmaker() as session:
            player = await self._find_player(session, game_id, user_id)
            return _player_record(player) if player else None

    async def update_player(self, game_id: str, user_id: str, **fields: Any) -> PlayerRecord:
        _check_fields(fields, PLAYER_UPDATABLE)
        async with self._sessionmaker.begin() as session:
            player = await self._find_player(session, game_id, user_id)
            if player is None:
                raise LookupError(f"Player {user_id} not in game {game_id}")
            for name, value in fields.items():
                setattr(player, name, value)
            await session.flush()
            await session.refresh(player)
            return _player_record(player)

    async def add_points(
        self, game_id: str, user_id: str, points: int, *, round_win: bool
    ) -> PlayerRecord:
        """Atomically add points (and a round win) to a player's totals."""
        async with self._sessionmaker.begin() as session:
            await session.execute(
                update(Player)
                .where(Player.game_id == game_id, Player.user_id == user_id)
                .values(
                    score=Player.score + points,
                    round_wins=Player.round_wins + (1 if round_win else 0),
                )
            )
            return await self._require_player(session, game_id, user_id)

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    async def _find_player(session: AsyncSession, game_id: str, user_id: str) -> Player | None:
        stmt = select(Player).where(Player.game_id == game_id, Player.user_id == user_id)
        player: Player | None = await session.scalar(stmt)
        return player

    async def _require_player(
        self, session: AsyncSession, game_id: str, user_id: str
    ) -> PlayerRecord:
        player = await self._find_player(session, game_id, user_id)
        if player is None:
            raise LookupError(f"Player {user_id} not in game {game_id}")
        await session.refresh(player)
        return _player_record(player)
