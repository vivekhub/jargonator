"""SQLAlchemy models for every table in spec.md §10.

Migrations in ``db/migrations`` are hand-written with plain SQLAlchemy types. A test checks
that they match these models (no autogenerate diffs).
"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from jargonator.db.types import StrEnumType, UTCDateTime
from jargonator.domain.state import GameState, JargonLevel, PlayerStatus, RoundStatus

ID = String(36)
SLACK_ID = String(32)


class Base(DeclarativeBase):
    pass


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        Index(
            "uq_games_active_channel",
            "channel_id",
            unique=True,
            sqlite_where=text("state != 'ENDED'"),
        ),
    )

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    channel_id: Mapped[str] = mapped_column(SLACK_ID)
    host_user_id: Mapped[str] = mapped_column(SLACK_ID)
    state: Mapped[GameState] = mapped_column(StrEnumType(GameState))
    created_by: Mapped[str] = mapped_column(SLACK_ID)
    guess_seconds: Mapped[int] = mapped_column(Integer)
    writer_seconds: Mapped[int] = mapped_column(Integer)
    join_window_seconds: Mapped[int] = mapped_column(Integer)
    lobby_message_ts: Mapped[str | None] = mapped_column(SLACK_ID)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    end_reason: Mapped[str | None] = mapped_column(String(16))
    lobby_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    idle_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_activity_at: Mapped[datetime] = mapped_column(UTCDateTime)
    last_writer: Mapped[str | None] = mapped_column(SLACK_ID)


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (
        UniqueConstraint("game_id", "user_id", name="uq_players_game_user"),
        Index("ix_players_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    game_id: Mapped[str] = mapped_column(ID, ForeignKey("games.id"))
    user_id: Mapped[str] = mapped_column(SLACK_ID)
    status: Mapped[PlayerStatus] = mapped_column(StrEnumType(PlayerStatus))
    score: Mapped[int] = mapped_column(Integer, default=0)
    round_wins: Mapped[int] = mapped_column(Integer, default=0)
    consecutive_misses: Mapped[int] = mapped_column(Integer, default=0)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime)
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class TurnOrderEntry(Base):
    __tablename__ = "turn_order"

    game_id: Mapped[str] = mapped_column(ID, ForeignKey("games.id"), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    cycle_no: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(SLACK_ID)
    consumed: Mapped[bool] = mapped_column(Boolean)


class Round(Base):
    __tablename__ = "rounds"
    __table_args__ = (UniqueConstraint("game_id", "number", name="uq_rounds_game_number"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    game_id: Mapped[str] = mapped_column(ID, ForeignKey("games.id"))
    number: Mapped[int] = mapped_column(Integer)
    writer_user_id: Mapped[str] = mapped_column(SLACK_ID)
    status: Mapped[RoundStatus] = mapped_column(StrEnumType(RoundStatus))
    level: Mapped[JargonLevel | None] = mapped_column(StrEnumType(JargonLevel))
    sentence: Mapped[str | None] = mapped_column(Text)
    jargon: Mapped[str | None] = mapped_column(Text)
    quip: Mapped[str | None] = mapped_column(Text)
    writer_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    writer_reminder_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    guess_deadline: Mapped[datetime | None] = mapped_column(UTCDateTime)
    host_claim_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    status_message_ts: Mapped[str | None] = mapped_column(SLACK_ID)
    results_message_ts: Mapped[str | None] = mapped_column(SLACK_ID)
    writer_bonus_awarded: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class RoundGuesser(Base):
    """Who received the jargon for a round (the primary key makes it unique per user)."""

    __tablename__ = "round_guessers"

    round_id: Mapped[str] = mapped_column(ID, ForeignKey("rounds.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(SLACK_ID, primary_key=True)
    dm_channel_id: Mapped[str] = mapped_column(SLACK_ID)
    dm_message_ts: Mapped[str | None] = mapped_column(SLACK_ID)


class Guess(Base):
    __tablename__ = "guesses"
    __table_args__ = (UniqueConstraint("round_id", "user_id", name="uq_guesses_round_user"),)

    id: Mapped[str] = mapped_column(ID, primary_key=True)
    round_id: Mapped[str] = mapped_column(ID, ForeignKey("rounds.id"))
    user_id: Mapped[str] = mapped_column(SLACK_ID)
    text: Mapped[str] = mapped_column(Text)
    submitted_at: Mapped[datetime] = mapped_column(UTCDateTime)
    moderated_out: Mapped[bool] = mapped_column(Boolean, default=False)
    similarity: Mapped[float | None] = mapped_column(Float)
    rank: Mapped[int | None] = mapped_column(Integer)
    points: Mapped[int] = mapped_column(Integer, default=0)
