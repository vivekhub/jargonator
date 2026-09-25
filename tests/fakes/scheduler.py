"""A Scheduler that just records what is scheduled (no tasks, no time)."""

from datetime import datetime
from typing import TYPE_CHECKING

from jargonator.engine.scheduler import TimerKind

if TYPE_CHECKING:
    from jargonator.engine.scheduler import Scheduler


class RecordingScheduler:
    def __init__(self) -> None:
        self.scheduled: dict[tuple[str, TimerKind], tuple[datetime, str | None]] = {}
        """(game_id, kind) → (when, round_id)"""
        self.history: list[tuple[str, str, TimerKind]] = []
        """(action, game_id, kind) in call order"""

    def schedule(self, game_id: str, kind: TimerKind, when: datetime, round_id: str | None) -> None:
        self.scheduled[(game_id, kind)] = (when, round_id)
        self.history.append(("schedule", game_id, kind))

    def cancel(self, game_id: str, kind: TimerKind) -> None:
        self.scheduled.pop((game_id, kind), None)
        self.history.append(("cancel", game_id, kind))

    def cancel_all(self, game_id: str) -> None:
        for key in [k for k in self.scheduled if k[0] == game_id]:
            del self.scheduled[key]
        self.history.append(("cancel_all", game_id, TimerKind.IDLE))

    def when(self, game_id: str, kind: TimerKind) -> datetime | None:
        entry = self.scheduled.get((game_id, kind))
        return entry[0] if entry else None


if TYPE_CHECKING:
    _protocol_check: Scheduler = RecordingScheduler()
