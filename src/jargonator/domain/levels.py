"""Jargon level schedule by round number (spec.md §3.5).

Rounds 1–3 are mild, 4–8 spicy and 9–13 unhinged. Then the 13-round cycle repeats (14–16
mild, and so on). Round numbers count every round, including skipped ones.
"""

from jargonator.domain.state import JargonLevel

SCHEDULE: tuple[tuple[JargonLevel, int], ...] = (
    (JargonLevel.MILD, 3),
    (JargonLevel.SPICY, 5),
    (JargonLevel.UNHINGED, 5),
)
CYCLE_LENGTH = sum(count for _, count in SCHEDULE)


def level_for_round(number: int) -> JargonLevel:
    """The jargon level for 1-based round ``number``."""
    if number < 1:
        raise ValueError("Round numbers start at 1")
    position = (number - 1) % CYCLE_LENGTH
    for level, count in SCHEDULE:
        if position < count:
            return level
        position -= count
    raise AssertionError("unreachable")
