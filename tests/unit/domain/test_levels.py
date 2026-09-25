import pytest

from jargonator.domain.levels import CYCLE_LENGTH, level_for_round
from jargonator.domain.state import JargonLevel

M, S, U = JargonLevel.MILD, JargonLevel.SPICY, JargonLevel.UNHINGED


def test_first_cycle_is_3_mild_5_spicy_5_unhinged() -> None:
    assert [level_for_round(n) for n in range(1, 14)] == [M] * 3 + [S] * 5 + [U] * 5
    assert CYCLE_LENGTH == 13


@pytest.mark.parametrize(
    ("number", "level"),
    [(14, M), (16, M), (17, S), (21, S), (22, U), (26, U), (27, M), (40, M)],
)
def test_cycle_restarts_after_round_13(number: int, level: JargonLevel) -> None:
    assert level_for_round(number) is level


def test_round_numbers_start_at_one() -> None:
    with pytest.raises(ValueError):
        level_for_round(0)
