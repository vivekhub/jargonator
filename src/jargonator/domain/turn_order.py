"""Shuffled round-robin turn order (spec.md §3.1, §3.3, §3.5).

All randomness comes from an injected ``random.Random`` so tests are deterministic.
"""

import random
from collections.abc import Collection, Sequence
from dataclasses import dataclass, field

TurnOrderRow = tuple[int, int, str, bool]
"""(cycle_no, position, user_id, consumed): the ``turn_order`` table shape (spec §10)."""


@dataclass
class TurnOrder:
    cycle_no: int
    queue: list[str]
    """Users still to write in the current cycle, in order."""
    written: list[str] = field(default_factory=list)
    """Users who have already written (been popped) in the current cycle."""
    last_writer: str | None = None

    @classmethod
    def new(cls, player_ids: Sequence[str], rng: random.Random) -> "TurnOrder":
        """Shuffle the players into cycle 1."""
        queue = sorted(set(player_ids))
        rng.shuffle(queue)
        return cls(cycle_no=1, queue=queue)

    def next_writer(self, active_ids: Collection[str], rng: random.Random) -> str | None:
        """Pop the next active writer, starting a new shuffled cycle when needed.

        Skips users not in ``active_ids``. A new cycle never starts with the previous
        writer when two or more players are active. Returns ``None`` if nobody is active.
        """
        if not active_ids:
            return None
        writer = self._pop_active(active_ids)
        if writer is None:
            self._start_new_cycle(active_ids, rng)
            writer = self._pop_active(active_ids)
        self.last_writer = writer
        return writer

    def append(self, user_id: str) -> None:
        """Add a mid-game joiner to the end of the current cycle.

        No-op if they are already queued or have already written this cycle (a player who
        leaves and rejoins after writing waits for the next cycle).
        """
        if user_id not in self.queue and user_id not in self.written:
            self.queue.append(user_id)

    def requeue_front(self, user_id: str) -> None:
        """Make a voided writer write next (spec §3.5)."""
        self.queue = [user_id, *(u for u in self.queue if u != user_id)]
        self.written = [u for u in self.written if u != user_id]

    def to_rows(self) -> list[TurnOrderRow]:
        """Serialise the current cycle: written users first (consumed), then the queue."""
        entries = [(u, True) for u in self.written] + [(u, False) for u in self.queue]
        return [(self.cycle_no, pos, user, done) for pos, (user, done) in enumerate(entries)]

    @classmethod
    def from_rows(cls, rows: Sequence[TurnOrderRow], last_writer: str | None) -> "TurnOrder":
        ordered = sorted(rows, key=lambda row: row[1])
        return cls(
            cycle_no=ordered[0][0] if ordered else 1,
            queue=[user for _, _, user, consumed in ordered if not consumed],
            written=[user for _, _, user, consumed in ordered if consumed],
            last_writer=last_writer,
        )

    def _pop_active(self, active_ids: Collection[str]) -> str | None:
        while self.queue:
            user = self.queue.pop(0)
            self.written.append(user)
            if user in active_ids:
                return user
            self.written.remove(user)  # skipped players have not written this cycle
        return None

    def _start_new_cycle(self, active_ids: Collection[str], rng: random.Random) -> None:
        queue = sorted(active_ids)
        rng.shuffle(queue)
        if len(queue) >= 2 and queue[0] == self.last_writer:
            queue[0], queue[1] = queue[1], queue[0]
        self.cycle_no += 1
        self.queue = queue
        self.written = []
