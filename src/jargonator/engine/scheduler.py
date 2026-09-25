"""Timer abstraction. Deadlines live in the DB; a Scheduler turns them into callbacks
(spec.md §9). The real implementation is ``engine/timers.TimerService``.
"""

from datetime import datetime
from enum import StrEnum
from typing import Protocol


class TimerKind(StrEnum):
    LOBBY = "lobby"
    WRITER_REMINDER = "writer_reminder"
    WRITER_DEADLINE = "writer_deadline"
    GUESS_DEADLINE = "guess_deadline"
    LLM_RETRY = "llm_retry"
    HOST_CLAIM = "host_claim"
    IDLE = "idle"
    RESUME = "resume"
    """Restart recovery: resume a step that was interrupted mid-way (spec §9)."""


class Scheduler(Protocol):
    def schedule(self, game_id: str, kind: TimerKind, when: datetime, round_id: str | None) -> None:
        """(Re)schedule a timer. Replaces any pending timer of the same kind for the game."""
        ...

    def cancel(self, game_id: str, kind: TimerKind) -> None: ...

    def cancel_all(self, game_id: str) -> None: ...
