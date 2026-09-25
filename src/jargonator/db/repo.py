"""Repository: the only code that talks to the database (spec.md §10).

Every public method runs in its own transaction and returns plain records.
"""

import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any, NamedTuple, Protocol

from sqlalchemy import case, delete, literal_column, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from jargonator.db.models import Game, Guess, Player, Round, RoundGuesser, TurnOrderEntry
from jargonator.db.records import (
    GameRecord,
    GuessRecord,
    PlayerRecord,
    RoundGuesserRecord,
    RoundRecord,
)
from jargonator.domain.state import GameState, PlayerStatus, RoundStatus
from jargonator.domain.turn_order import TurnOrder


class ChannelBusyError(Exception):
    """The channel already has an active game (spec §3.2)."""


class UserInOtherGameError(Exception):
    """The user is already active (or inactive) in another game (spec §3.2)."""

    def __init__(self, game: GameRecord) -> None:
        super().__init__(f"User is already in the game in {game.channel_id}")
        self.game = game


class DuplicateGuessError(Exception):
    """The user has already guessed in this round (spec §3.6: one final guess)."""


class GuessResult(NamedTuple):
    guess_id: str
    score: int | None
    rank: int | None
    points: int
    moderated_out: bool


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
ROUND_UPDATABLE = frozenset(
    {
        "status",
        "level",
        "sentence",
        "jargon",
        "quip",
        "guess_deadline",
        "host_claim_at",
        "llm_retry_at",
        "status_message_ts",
        "results_message_ts",
        "writer_bonus_awarded",
        "ended_at",
        "writer_deadline",
        "writer_reminder_at",
    }
)


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
        lobby_deadline: datetime | None = None,
        idle_deadline: datetime | None = None,
    ) -> GameRecord: ...

    async def get_game(self, game_id: str) -> GameRecord | None: ...

    async def get_active_game_by_channel(self, channel_id: str) -> GameRecord | None: ...

    async def update_game(self, game_id: str, **fields: Any) -> GameRecord: ...

    async def find_active_game_for_user(self, user_id: str) -> GameRecord | None: ...

    async def list_non_ended_games(self) -> list[GameRecord]: ...

    async def join_game(self, game_id: str, user_id: str, now: datetime) -> PlayerRecord: ...

    async def get_players(self, game_id: str) -> list[PlayerRecord]: ...

    async def get_player(self, game_id: str, user_id: str) -> PlayerRecord | None: ...

    async def update_player(self, game_id: str, user_id: str, **fields: Any) -> PlayerRecord: ...

    async def add_points(
        self, game_id: str, user_id: str, points: int, *, round_win: bool
    ) -> PlayerRecord: ...

    async def create_round(
        self,
        *,
        game_id: str,
        number: int,
        writer_user_id: str,
        writer_deadline: datetime | None,
        writer_reminder_at: datetime | None,
        now: datetime,
    ) -> RoundRecord: ...

    async def get_round(self, round_id: str) -> RoundRecord | None: ...

    async def get_current_round(self, game_id: str) -> RoundRecord | None: ...

    async def list_rounds(self, game_id: str) -> list[RoundRecord]: ...

    async def update_round(self, round_id: str, **fields: Any) -> RoundRecord: ...

    async def add_round_guessers(
        self, round_id: str, guessers: Sequence[tuple[str, str]]
    ) -> None: ...

    async def set_guesser_dm_ts(self, round_id: str, user_id: str, ts: str) -> None: ...

    async def get_round_guessers(self, round_id: str) -> list[RoundGuesserRecord]: ...

    async def add_guess(
        self, round_id: str, user_id: str, text: str, now: datetime
    ) -> GuessRecord: ...

    async def get_guesses(self, round_id: str) -> list[GuessRecord]: ...

    async def save_guess_results(self, round_id: str, results: Sequence[GuessResult]) -> None: ...

    async def save_turn_order(self, game_id: str, turn_order: TurnOrder) -> None: ...

    async def load_turn_order(self, game_id: str) -> TurnOrder | None: ...


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
        last_writer=g.last_writer,
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


def _round_record(r: Round) -> RoundRecord:
    return RoundRecord(
        id=r.id,
        game_id=r.game_id,
        number=r.number,
        writer_user_id=r.writer_user_id,
        status=r.status,
        level=r.level,
        sentence=r.sentence,
        jargon=r.jargon,
        quip=r.quip,
        writer_deadline=r.writer_deadline,
        writer_reminder_at=r.writer_reminder_at,
        guess_deadline=r.guess_deadline,
        host_claim_at=r.host_claim_at,
        llm_retry_at=r.llm_retry_at,
        status_message_ts=r.status_message_ts,
        results_message_ts=r.results_message_ts,
        writer_bonus_awarded=r.writer_bonus_awarded,
        started_at=r.started_at,
        ended_at=r.ended_at,
    )


def _guess_record(g: Guess) -> GuessRecord:
    return GuessRecord(
        id=g.id,
        round_id=g.round_id,
        user_id=g.user_id,
        text=g.text,
        submitted_at=g.submitted_at,
        moderated_out=g.moderated_out,
        score=g.score,
        rank=g.rank,
        points=g.points,
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
        lobby_deadline: datetime | None = None,
        idle_deadline: datetime | None = None,
    ) -> GameRecord:
        """Create a LOBBY game with the host as its first player, in one transaction.

        Raises ``UserInOtherGameError`` if the host is in another game, and
        ``ChannelBusyError`` if the channel already has an active game.
        """
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
            lobby_deadline=lobby_deadline,
            idle_deadline=idle_deadline,
        )
        async with self._sessionmaker.begin() as session:
            other = await self._active_game_for_user(session, host_user_id)
            if other is not None:
                raise UserInOtherGameError(_game_record(other))
            session.add(game)
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ChannelBusyError(channel_id) from exc
            await session.execute(self._upsert_player_stmt(game.id, host_user_id, now))
            await session.refresh(game)
            return _game_record(game)

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
            game = await self._active_game_for_user(session, user_id)
            return _game_record(game) if game else None

    async def list_non_ended_games(self) -> list[GameRecord]:
        async with self._sessionmaker() as session:
            games = await session.scalars(
                select(Game).where(Game.state != GameState.ENDED).order_by(Game.created_at)
            )
            return [_game_record(g) for g in games]

    # --- players ----------------------------------------------------------------------

    async def join_game(self, game_id: str, user_id: str, now: datetime) -> PlayerRecord:
        """Add a player, or reactivate one who left or went inactive, atomically.

        Raises ``UserInOtherGameError`` if the user is in a different active game, and
        ``LookupError`` for an unknown game. Score and round wins are kept. Misses reset
        (unless already active). A player returning from ``left`` gets a fresh
        ``joined_at``, so they don't count as longest-standing for host transfer (§3.10).
        """
        async with self._sessionmaker.begin() as session:
            if await session.get(Game, game_id) is None:
                raise LookupError(f"Game {game_id} not found")
            other = await self._active_game_for_user(session, user_id, exclude_game_id=game_id)
            if other is not None:
                raise UserInOtherGameError(_game_record(other))
            await session.execute(self._upsert_player_stmt(game_id, user_id, now))
            return await self._require_player(session, game_id, user_id)

    @staticmethod
    def _upsert_player_stmt(game_id: str, user_id: str, now: datetime) -> Any:
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
        return stmt

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

    # --- rounds -----------------------------------------------------------------------

    async def create_round(
        self,
        *,
        game_id: str,
        number: int,
        writer_user_id: str,
        writer_deadline: datetime | None,
        writer_reminder_at: datetime | None,
        now: datetime,
    ) -> RoundRecord:
        rnd = Round(
            id=str(uuid.uuid4()),
            game_id=game_id,
            number=number,
            writer_user_id=writer_user_id,
            status=RoundStatus.AWAITING_SENTENCE,
            writer_deadline=writer_deadline,
            writer_reminder_at=writer_reminder_at,
            writer_bonus_awarded=False,
            started_at=now,
        )
        async with self._sessionmaker.begin() as session:
            session.add(rnd)
            await session.flush()
            await session.refresh(rnd)
            return _round_record(rnd)

    async def get_round(self, round_id: str) -> RoundRecord | None:
        async with self._sessionmaker() as session:
            rnd = await session.get(Round, round_id)
            return _round_record(rnd) if rnd else None

    async def get_current_round(self, game_id: str) -> RoundRecord | None:
        async with self._sessionmaker() as session:
            rnd = await session.scalar(
                select(Round).where(Round.game_id == game_id).order_by(Round.number.desc()).limit(1)
            )
            return _round_record(rnd) if rnd else None

    async def list_rounds(self, game_id: str) -> list[RoundRecord]:
        async with self._sessionmaker() as session:
            rounds = await session.scalars(
                select(Round).where(Round.game_id == game_id).order_by(Round.number)
            )
            return [_round_record(r) for r in rounds]

    async def update_round(self, round_id: str, **fields: Any) -> RoundRecord:
        _check_fields(fields, ROUND_UPDATABLE)
        async with self._sessionmaker.begin() as session:
            rnd = await session.get(Round, round_id)
            if rnd is None:
                raise LookupError(f"Round {round_id} not found")
            for name, value in fields.items():
                setattr(rnd, name, value)
            await session.flush()
            await session.refresh(rnd)
            return _round_record(rnd)

    # --- guessers and guesses -------------------------------------------------------------

    async def add_round_guessers(self, round_id: str, guessers: Sequence[tuple[str, str]]) -> None:
        """Record who received the jargon: ``(user_id, dm_channel_id)`` pairs."""
        async with self._sessionmaker.begin() as session:
            session.add_all(
                RoundGuesser(round_id=round_id, user_id=user_id, dm_channel_id=dm_channel)
                for user_id, dm_channel in guessers
            )

    async def set_guesser_dm_ts(self, round_id: str, user_id: str, ts: str) -> None:
        async with self._sessionmaker.begin() as session:
            await session.execute(
                update(RoundGuesser)
                .where(RoundGuesser.round_id == round_id, RoundGuesser.user_id == user_id)
                .values(dm_message_ts=ts)
            )

    async def get_round_guessers(self, round_id: str) -> list[RoundGuesserRecord]:
        async with self._sessionmaker() as session:
            rows = await session.scalars(
                select(RoundGuesser)
                .where(RoundGuesser.round_id == round_id)
                .order_by(RoundGuesser.user_id)
            )
            return [
                RoundGuesserRecord(r.round_id, r.user_id, r.dm_channel_id, r.dm_message_ts)
                for r in rows
            ]

    async def add_guess(self, round_id: str, user_id: str, text: str, now: datetime) -> GuessRecord:
        guess = Guess(
            id=str(uuid.uuid4()),
            round_id=round_id,
            user_id=user_id,
            text=text,
            submitted_at=now,
            moderated_out=False,
            points=0,
        )
        try:
            async with self._sessionmaker.begin() as session:
                session.add(guess)
                await session.flush()
                await session.refresh(guess)
                return _guess_record(guess)
        except IntegrityError as exc:
            if "UNIQUE" in str(exc.orig):
                raise DuplicateGuessError(f"{user_id} already guessed in {round_id}") from exc
            raise

    async def get_guesses(self, round_id: str) -> list[GuessRecord]:
        """All guesses for a round, in submission order."""
        async with self._sessionmaker() as session:
            guesses = await session.scalars(
                select(Guess)
                .where(Guess.round_id == round_id)
                # rowid = insertion order, so identical timestamps stay deterministic
                .order_by(Guess.submitted_at, literal_column("guesses.rowid"))
            )
            return [_guess_record(g) for g in guesses]

    async def save_guess_results(self, round_id: str, results: Sequence[GuessResult]) -> None:
        async with self._sessionmaker.begin() as session:
            for result in results:
                await session.execute(
                    update(Guess)
                    .where(Guess.id == result.guess_id, Guess.round_id == round_id)
                    .values(
                        score=result.score,
                        rank=result.rank,
                        points=result.points,
                        moderated_out=result.moderated_out,
                    )
                )

    # --- turn order ---------------------------------------------------------------------

    async def save_turn_order(self, game_id: str, turn_order: TurnOrder) -> None:
        """Replace the stored turn order (and the game's last writer) atomically."""
        async with self._sessionmaker.begin() as session:
            await session.execute(delete(TurnOrderEntry).where(TurnOrderEntry.game_id == game_id))
            session.add_all(
                TurnOrderEntry(
                    game_id=game_id,
                    cycle_no=cycle_no,
                    position=position,
                    user_id=user_id,
                    consumed=consumed,
                )
                for cycle_no, position, user_id, consumed in turn_order.to_rows()
            )
            await session.execute(
                update(Game).where(Game.id == game_id).values(last_writer=turn_order.last_writer)
            )

    async def load_turn_order(self, game_id: str) -> TurnOrder | None:
        """Return the stored turn order, or ``None`` if the game hasn't started."""
        async with self._sessionmaker() as session:
            rows = list(
                await session.scalars(
                    select(TurnOrderEntry)
                    .where(TurnOrderEntry.game_id == game_id)
                    .order_by(TurnOrderEntry.position)
                )
            )
            if not rows:
                return None
            last_writer = await session.scalar(select(Game.last_writer).where(Game.id == game_id))
            return TurnOrder.from_rows(
                [(r.cycle_no, r.position, r.user_id, r.consumed) for r in rows], last_writer
            )

    # --- helpers ----------------------------------------------------------------------

    @staticmethod
    async def _active_game_for_user(
        session: AsyncSession, user_id: str, exclude_game_id: str | None = None
    ) -> Game | None:
        stmt = (
            select(Game)
            .join(Player, Player.game_id == Game.id)
            .where(
                Player.user_id == user_id,
                Player.status.in_([PlayerStatus.ACTIVE, PlayerStatus.INACTIVE]),
                Game.state != GameState.ENDED,
            )
            .limit(1)
        )
        if exclude_game_id is not None:
            stmt = stmt.where(Game.id != exclude_game_id)
        game: Game | None = await session.scalar(stmt)
        return game

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
