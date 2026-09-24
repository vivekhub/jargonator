from datetime import UTC, datetime, timedelta

import pytest

from jargonator.domain.scoring import (
    GuessInput,
    ScoringRules,
    apply_cluster_orders,
    find_tie_clusters,
    score_round,
    sort_valid,
)

T0 = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
RULES = ScoringRules(
    points_by_rank=(10, 5, 1), tie_margin=0.02, writer_bonus_threshold=0.5, writer_bonus_points=10
)


def g(gid: str, sim: float, t: int = 0, moderated: bool = False) -> GuessInput:
    return GuessInput(
        guess_id=gid,
        user_id=f"U{gid}",
        similarity=sim,
        submitted_at=T0 + timedelta(seconds=t),
        moderated_out=moderated,
    )


def ranks(outcome_placements: list[object]) -> list[tuple[str, int | None, int]]:
    return [(p.guess_id, p.rank, p.points) for p in outcome_placements]  # type: ignore[attr-defined]


# --- sort_valid -------------------------------------------------------------


def test_sort_valid_drops_moderated_and_sorts() -> None:
    guesses = [g("a", 0.3, 1), g("b", 0.9, 2), g("c", 0.99, 0, moderated=True), g("d", 0.3, 0)]
    assert [x.guess_id for x in sort_valid(guesses)] == ["b", "d", "a"]


# --- find_tie_clusters ------------------------------------------------------


def test_no_clusters_when_well_separated() -> None:
    assert find_tie_clusters(sort_valid([g("a", 0.9), g("b", 0.7), g("c", 0.5)]), 0.02) == []


def test_cluster_in_top_three() -> None:
    s = sort_valid([g("a", 0.80), g("b", 0.79), g("c", 0.50)])
    assert find_tie_clusters(s, 0.02) == [["a", "b"]]


def test_exact_margin_counts_as_tie_despite_float_error() -> None:
    s = sort_valid([g("a", 0.80), g("b", 0.78)])  # 0.80 - 0.78 == 0.0200000000000000018
    assert find_tie_clusters(s, 0.02) == [["a", "b"]]


def test_cluster_measured_from_its_top_member() -> None:
    # 0.80 → 0.79 → 0.78 are all within 0.02 of 0.80; 0.77 is not.
    s = sort_valid([g("a", 0.80), g("b", 0.79), g("c", 0.78), g("d", 0.77)])
    assert find_tie_clusters(s, 0.02) == [["a", "b", "c"]]


def test_tie_outside_top_three_ignored() -> None:
    s = sort_valid([g("a", 0.9), g("b", 0.7), g("c", 0.5), g("d", 0.3), g("e", 0.29)])
    assert find_tie_clusters(s, 0.02) == []


def test_tie_spanning_positions_three_and_four_included() -> None:
    s = sort_valid([g("a", 0.9), g("b", 0.7), g("c", 0.5), g("d", 0.49)])
    assert find_tie_clusters(s, 0.02) == [["c", "d"]]


def test_multiple_clusters() -> None:
    s = sort_valid([g("a", 0.9), g("b", 0.89), g("c", 0.5), g("d", 0.495)])
    assert find_tie_clusters(s, 0.02) == [["a", "b"], ["c", "d"]]


# --- apply_cluster_orders ---------------------------------------------------


def test_valid_llm_order_applied() -> None:
    s = sort_valid([g("a", 0.80, 1), g("b", 0.79, 2), g("c", 0.5)])
    clusters = find_tie_clusters(s, 0.02)
    out = apply_cluster_orders(s, clusters, {0: ["b", "a"]})
    assert [x.guess_id for x in out] == ["b", "a", "c"]


@pytest.mark.parametrize("order", [None, ["a", "zzz"], ["a"], ["a", "a"], ["a", "b", "c"]])
def test_invalid_or_missing_order_falls_back_to_submission_time(order: list[str] | None) -> None:
    # "a" scores higher but "b" was submitted first.
    s = sort_valid([g("a", 0.80, t=5), g("b", 0.79, t=1), g("c", 0.5)])
    clusters = find_tie_clusters(s, 0.02)
    out = apply_cluster_orders(s, clusters, {0: order})
    assert [x.guess_id for x in out] == ["b", "a", "c"]


def test_missing_key_falls_back_to_submission_time() -> None:
    s = sort_valid([g("a", 0.80, t=5), g("b", 0.79, t=1)])
    out = apply_cluster_orders(s, find_tie_clusters(s, 0.02), {})
    assert [x.guess_id for x in out] == ["b", "a"]


# --- score_round ------------------------------------------------------------


def test_standard_points() -> None:
    out = score_round([g("a", 0.9), g("b", 0.7), g("c", 0.6), g("d", 0.55)], RULES, {})
    assert ranks(out.placements) == [("a", 1, 10), ("b", 2, 5), ("c", 3, 1), ("d", 4, 0)]
    assert out.writer_bonus == 0
    assert out.best_similarity == 0.9


@pytest.mark.parametrize(
    ("sims", "expected"),
    [([0.9], [10]), ([0.9, 0.2], [10, 5])],
)
def test_fewer_than_three_guesses(sims: list[float], expected: list[int]) -> None:
    out = score_round([g(str(i), s) for i, s in enumerate(sims)], RULES, {})
    assert [p.points for p in out.placements] == expected


def test_no_minimum_similarity_for_guessers() -> None:
    out = score_round([g("a", 0.05)], RULES, {})
    assert out.placements[0].points == 10


def test_moderated_guesses_get_no_rank_or_points() -> None:
    out = score_round([g("a", 0.99, moderated=True), g("b", 0.6), g("c", 0.55)], RULES, {})
    assert ranks(out.placements) == [("b", 1, 10), ("c", 2, 5), ("a", None, 0)]
    assert out.best_similarity == 0.6


def test_llm_tiebreak_order_changes_winner() -> None:
    guesses = [g("a", 0.80, 1), g("b", 0.79, 2), g("c", 0.3)]
    out = score_round(guesses, RULES, {0: ["b", "a"]})
    assert ranks(out.placements)[:2] == [("b", 1, 10), ("a", 2, 5)]


def test_similarities_passed_through_unchanged() -> None:
    out = score_round([g("a", 0.123456789)], RULES, {})
    assert out.placements[0].similarity == 0.123456789


@pytest.mark.parametrize(
    ("best", "bonus"),
    [(0.4999, 10), (0.5, 0), (0.8, 0)],
)
def test_writer_bonus_threshold(best: float, bonus: int) -> None:
    out = score_round([g("a", best), g("b", 0.1)], RULES, {})
    assert out.writer_bonus == bonus


def test_no_writer_bonus_without_valid_guesses() -> None:
    assert score_round([], RULES, {}).writer_bonus == 0
    assert score_round([], RULES, {}).best_similarity is None
    all_moderated = score_round([g("a", 0.1, moderated=True)], RULES, {})
    assert all_moderated.writer_bonus == 0
    assert all_moderated.best_similarity is None


def test_writer_bonus_and_guesser_points_in_same_round() -> None:
    out = score_round([g("a", 0.3), g("b", 0.2)], RULES, {})
    assert out.writer_bonus == 10
    assert [p.points for p in out.placements] == [10, 5]


def test_custom_points_table() -> None:
    rules = ScoringRules((3, 2, 1), 0.02, 0.5, 10)
    out = score_round([g("a", 0.9), g("b", 0.7), g("c", 0.6)], rules, {})
    assert [p.points for p in out.placements] == [3, 2, 1]
