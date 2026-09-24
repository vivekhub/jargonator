"""Round ranking, points and the writer bonus (spec.md §3.7).

The LLM judge is not called here. The engine gets 0-100 scores from the judge (spec §7.4)
and passes them in, keeping this module pure.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class GuessInput:
    guess_id: str
    user_id: str
    score: int
    """The judge's 0-100 rating (0 for moderated guesses, which are never judged)."""
    submitted_at: datetime
    moderated_out: bool


@dataclass(frozen=True)
class Placement:
    guess_id: str
    user_id: str
    rank: int | None
    """1-based position among valid guesses. ``None`` for moderated guesses."""
    points: int
    score: int
    moderated_out: bool


@dataclass(frozen=True)
class RoundOutcome:
    placements: list[Placement]
    """Valid guesses in rank order, followed by moderated guesses."""
    writer_bonus: int
    best_score: int | None


@dataclass(frozen=True)
class ScoringRules:
    points_by_rank: tuple[int, ...]
    writer_bonus_threshold: int
    writer_bonus_points: int


def sort_valid(guesses: Sequence[GuessInput]) -> list[GuessInput]:
    """Drop moderated guesses, then sort by score (desc). Exact ties go to the earlier guess."""
    valid = [x for x in guesses if not x.moderated_out]
    return sorted(valid, key=lambda x: (-x.score, x.submitted_at))


def score_round(guesses: Sequence[GuessInput], rules: ScoringRules) -> RoundOutcome:
    """Rank the guesses, award points by rank and decide the writer bonus."""
    ranked = sort_valid(guesses)
    placements = [
        Placement(
            guess_id=x.guess_id,
            user_id=x.user_id,
            rank=position,
            points=rules.points_by_rank[position - 1]
            if position <= len(rules.points_by_rank)
            else 0,
            score=x.score,
            moderated_out=False,
        )
        for position, x in enumerate(ranked, start=1)
    ]
    placements += [
        Placement(x.guess_id, x.user_id, None, 0, x.score, True) for x in guesses if x.moderated_out
    ]
    best = ranked[0].score if ranked else None
    bonus = (
        rules.writer_bonus_points if best is not None and best < rules.writer_bonus_threshold else 0
    )
    return RoundOutcome(placements=placements, writer_bonus=bonus, best_score=best)
