"""Plain, immutable records returned by the repository (never ORM objects)."""

from dataclasses import dataclass
from datetime import datetime

from jargonator.domain.state import GameState, JargonLevel, PlayerStatus, RoundStatus


@dataclass(frozen=True)
class GameRecord:
    id: str
    channel_id: str
    host_user_id: str
    state: GameState
    created_by: str
    guess_seconds: int
    writer_seconds: int
    join_window_seconds: int
    lobby_message_ts: str | None
    created_at: datetime
    started_at: datetime | None
    ended_at: datetime | None
    end_reason: str | None
    lobby_deadline: datetime | None
    idle_deadline: datetime | None
    last_activity_at: datetime
    last_writer: str | None


@dataclass(frozen=True)
class PlayerRecord:
    game_id: str
    user_id: str
    status: PlayerStatus
    score: int
    round_wins: int
    consecutive_misses: int
    joined_at: datetime
    left_at: datetime | None


@dataclass(frozen=True)
class RoundRecord:
    id: str
    game_id: str
    number: int
    writer_user_id: str
    status: RoundStatus
    level: JargonLevel | None
    sentence: str | None
    jargon: str | None
    quip: str | None
    writer_deadline: datetime | None
    writer_reminder_at: datetime | None
    guess_deadline: datetime | None
    host_claim_at: datetime | None
    status_message_ts: str | None
    results_message_ts: str | None
    writer_bonus_awarded: bool
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True)
class RoundGuesserRecord:
    round_id: str
    user_id: str
    dm_channel_id: str
    dm_message_ts: str | None


@dataclass(frozen=True)
class GuessRecord:
    id: str
    round_id: str
    user_id: str
    text: str
    submitted_at: datetime
    moderated_out: bool
    similarity: float | None
    rank: int | None
    points: int
