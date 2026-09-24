"""Plain, immutable records returned by the repository (never ORM objects)."""

from dataclasses import dataclass
from datetime import datetime

from jargonator.domain.state import GameState, PlayerStatus


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
