import itertools

import pytest

from jargonator.domain.state import (
    ACTIVE_STATES,
    TRANSITIONS,
    GameEvent,
    GameState,
    JargonLevel,
    PlayerStatus,
    RoundStatus,
    transition,
)

S = GameState
E = GameEvent

VALID = {
    (S.LOBBY, E.START): S.AWAITING_SENTENCE,
    (S.AWAITING_SENTENCE, E.SENTENCE_ACCEPTED): S.GENERATING,
    (S.AWAITING_SENTENCE, E.WRITER_TIMEOUT): S.AWAITING_NEXT,
    (S.GENERATING, E.JARGON_READY): S.GUESSING,
    (S.GENERATING, E.JARGON_FAILED): S.AWAITING_NEXT,
    (S.GUESSING, E.GUESSING_CLOSED): S.JUDGING,
    (S.JUDGING, E.JUDGED): S.AWAITING_NEXT,
    (S.AWAITING_NEXT, E.NEXT_ROUND): S.AWAITING_SENTENCE,
    (S.AWAITING_NEXT, E.NEXT_ROUND_INSUFFICIENT_PLAYERS): S.PAUSED_PLAYERS,
    (S.PAUSED_PLAYERS, E.NEXT_ROUND): S.AWAITING_SENTENCE,
    (S.PAUSED_PLAYERS, E.NEXT_ROUND_INSUFFICIENT_PLAYERS): S.PAUSED_PLAYERS,
    **{(state, E.END): S.ENDED for state in S if state is not S.ENDED},
}


@pytest.mark.parametrize(("key", "expected"), list(VALID.items()), ids=str)
def test_valid_transitions(key: tuple[GameState, GameEvent], expected: GameState) -> None:
    assert transition(*key) is expected


def test_table_is_exactly_the_spec() -> None:
    assert dict(TRANSITIONS) == VALID


def test_every_other_pair_is_invalid() -> None:
    for state, event in itertools.product(S, E):
        if (state, event) not in VALID:
            assert transition(state, event) is None, (state, event)


def test_ended_is_terminal() -> None:
    assert all(transition(S.ENDED, event) is None for event in E)


def test_active_states() -> None:
    assert frozenset(S) - {S.ENDED} == ACTIVE_STATES


def test_enum_values_are_stable_strings() -> None:
    # These values are persisted in the database, so they must not change.
    assert [s.value for s in S] == [
        "LOBBY", "AWAITING_SENTENCE", "GENERATING", "GUESSING",
        "JUDGING", "AWAITING_NEXT", "PAUSED_PLAYERS", "ENDED",
    ]  # fmt: skip
    assert [r.value for r in RoundStatus] == [
        "awaiting_sentence", "generating", "guessing", "judging",
        "completed", "skipped", "voided",
    ]  # fmt: skip
    assert [p.value for p in PlayerStatus] == ["active", "inactive", "left"]
    assert [lv.value for lv in JargonLevel] == ["mild", "spicy", "unhinged"]
