from datetime import UTC, datetime, timedelta

import pytest

from jargonator.domain.scoring import GuessInput, Placement, ScoringRules, score_round, sort_valid

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RULES = ScoringRules(points_by_rank=(10, 5, 1), writer_bonus_threshold=50, writer_bonus_points=10)


def g(gid: str, score: int, t: int = 0, moderated: bool = False) -> GuessInput:
    return GuessInput(
        guess_id=gid,
        user_id=f"U{gid}",
        score=score,
        submitted_at=T0 + timedelta(seconds=t),
        moderated_out=moderated,
    )


def ranks(placements: list[Placement]) -> list[tuple[str, int | None, int]]:
    return [(p.guess_id, p.rank, p.points) for p in placements]


def test_sort_valid_drops_moderated_and_sorts() -> None:
    guesses = [g("a", 30, 1), g("b", 90, 2), g("c", 99, 0, moderated=True), g("d", 30, 0)]
    assert [x.guess_id for x in sort_valid(guesses)] == ["b", "d", "a"]


def test_standard_points() -> None:
    out = score_round([g("a", 90), g("b", 70), g("c", 60), g("d", 55)], RULES)
    assert ranks(out.placements) == [("a", 1, 10), ("b", 2, 5), ("c", 3, 1), ("d", 4, 0)]
    assert out.writer_bonus == 0
    assert out.best_score == 90


@pytest.mark.parametrize(("scores", "expected"), [([90], [10]), ([90, 20], [10, 5])])
def test_fewer_than_three_guesses(scores: list[int], expected: list[int]) -> None:
    out = score_round([g(str(i), s) for i, s in enumerate(scores)], RULES)
    assert [p.points for p in out.placements] == expected


def test_no_minimum_score_for_guessers() -> None:
    assert score_round([g("a", 5)], RULES).placements[0].points == 10


def test_exact_tie_goes_to_earlier_submission() -> None:
    out = score_round([g("late", 80, t=9), g("early", 80, t=2), g("c", 10)], RULES)
    assert ranks(out.placements) == [("early", 1, 10), ("late", 2, 5), ("c", 3, 1)]


def test_moderated_guesses_get_no_rank_or_points() -> None:
    out = score_round([g("a", 99, moderated=True), g("b", 60), g("c", 55)], RULES)
    assert ranks(out.placements) == [("b", 1, 10), ("c", 2, 5), ("a", None, 0)]
    assert out.placements[2].moderated_out is True
    assert out.best_score == 60


def test_scores_passed_through() -> None:
    assert score_round([g("a", 42)], RULES).placements[0].score == 42


@pytest.mark.parametrize(("best", "bonus"), [(49, 10), (50, 0), (80, 0)])
def test_writer_bonus_threshold(best: int, bonus: int) -> None:
    assert score_round([g("a", best), g("b", 10)], RULES).writer_bonus == bonus


def test_no_writer_bonus_without_valid_guesses() -> None:
    empty = score_round([], RULES)
    assert (empty.writer_bonus, empty.best_score) == (0, None)
    all_moderated = score_round([g("a", 10, moderated=True)], RULES)
    assert (all_moderated.writer_bonus, all_moderated.best_score) == (0, None)


def test_writer_bonus_and_guesser_points_in_same_round() -> None:
    out = score_round([g("a", 30), g("b", 20)], RULES)
    assert out.writer_bonus == 10
    assert [p.points for p in out.placements] == [10, 5]


def test_custom_points_table() -> None:
    rules = ScoringRules((3, 2, 1), 50, 10)
    out = score_round([g("a", 90), g("b", 70), g("c", 60)], rules)
    assert [p.points for p in out.placements] == [3, 2, 1]
