"""Game state machine: states, events and the transition table (spec.md §5).

Enum values are persisted in the database and must never change.
"""

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class GameState(StrEnum):
    LOBBY = "LOBBY"
    AWAITING_SENTENCE = "AWAITING_SENTENCE"
    GENERATING = "GENERATING"
    GUESSING = "GUESSING"
    JUDGING = "JUDGING"
    AWAITING_NEXT = "AWAITING_NEXT"
    PAUSED_PLAYERS = "PAUSED_PLAYERS"
    ENDED = "ENDED"


class RoundStatus(StrEnum):
    AWAITING_SENTENCE = "awaiting_sentence"
    GENERATING = "generating"
    GUESSING = "guessing"
    JUDGING = "judging"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    VOIDED = "voided"


class PlayerStatus(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"
    LEFT = "left"


class JargonLevel(StrEnum):
    MILD = "mild"
    SPICY = "spicy"
    UNHINGED = "unhinged"


class GameEvent(StrEnum):
    START = "START"
    SENTENCE_ACCEPTED = "SENTENCE_ACCEPTED"
    WRITER_TIMEOUT = "WRITER_TIMEOUT"
    JARGON_READY = "JARGON_READY"
    JARGON_FAILED = "JARGON_FAILED"
    GUESSING_CLOSED = "GUESSING_CLOSED"
    JUDGED = "JUDGED"
    NEXT_ROUND = "NEXT_ROUND"
    NEXT_ROUND_INSUFFICIENT_PLAYERS = "NEXT_ROUND_INSUFFICIENT_PLAYERS"
    END = "END"


ACTIVE_STATES: frozenset[GameState] = frozenset(s for s in GameState if s is not GameState.ENDED)

_S = GameState
_E = GameEvent

TRANSITIONS: Mapping[tuple[GameState, GameEvent], GameState] = MappingProxyType(
    {
        (_S.LOBBY, _E.START): _S.AWAITING_SENTENCE,
        (_S.AWAITING_SENTENCE, _E.SENTENCE_ACCEPTED): _S.GENERATING,
        (_S.AWAITING_SENTENCE, _E.WRITER_TIMEOUT): _S.AWAITING_NEXT,
        (_S.GENERATING, _E.JARGON_READY): _S.GUESSING,
        (_S.GENERATING, _E.JARGON_FAILED): _S.AWAITING_NEXT,
        (_S.GUESSING, _E.GUESSING_CLOSED): _S.JUDGING,
        # JUDGED also covers a round voided during judging (e.g. the scorer failed).
        (_S.JUDGING, _E.JUDGED): _S.AWAITING_NEXT,
        (_S.AWAITING_NEXT, _E.NEXT_ROUND): _S.AWAITING_SENTENCE,
        (_S.AWAITING_NEXT, _E.NEXT_ROUND_INSUFFICIENT_PLAYERS): _S.PAUSED_PLAYERS,
        (_S.PAUSED_PLAYERS, _E.NEXT_ROUND): _S.AWAITING_SENTENCE,
        (_S.PAUSED_PLAYERS, _E.NEXT_ROUND_INSUFFICIENT_PLAYERS): _S.PAUSED_PLAYERS,
        **{(state, _E.END): _S.ENDED for state in ACTIVE_STATES},
    }
)


def transition(state: GameState, event: GameEvent) -> GameState | None:
    """Return the next state, or ``None`` if ``event`` is not valid in ``state``."""
    return TRANSITIONS.get((state, event))
