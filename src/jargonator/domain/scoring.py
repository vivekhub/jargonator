"""Round ranking, tie clusters, points and the writer bonus (spec.md §3.7).

The LLM tie-breaker is not called here. The caller finds clusters with
``find_tie_clusters``, asks the LLM to order each one, and passes the orderings to
``score_round``. That keeps this module pure.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

_FLOAT_TOLERANCE = 1e-9
"""Absorbs float error so that e.g. 0.80 - 0.78 counts as exactly 0.02 apart."""


@dataclass(frozen=True)
class GuessInput:
    guess_id: str
    user_id: str
    similarity: float
    submitted_at: datetime
    moderated_out: bool


@dataclass(frozen=True)
class Placement:
    guess_id: str
    user_id: str
    rank: int | None
    """1-based position among valid guesses. ``None`` for moderated guesses."""
    points: int
    similarity: float
    moderated_out: bool


@dataclass(frozen=True)
class RoundOutcome:
    placements: list[Placement]
    """Valid guesses in rank order, followed by moderated guesses."""
    writer_bonus: int
    best_similarity: float | None


@dataclass(frozen=True)
class ScoringRules:
    points_by_rank: tuple[int, ...]
    tie_margin: float
    writer_bonus_threshold: float
    writer_bonus_points: int


ClusterOrders = Mapping[int, Sequence[str] | None]
"""LLM orderings keyed by cluster index (the list order from ``find_tie_clusters``)."""


def sort_valid(guesses: Sequence[GuessInput]) -> list[GuessInput]:
    """Drop moderated guesses, then sort by similarity (desc) then submission time (asc)."""
    valid = [x for x in guesses if not x.moderated_out]
    return sorted(valid, key=lambda x: (-x.similarity, x.submitted_at))


def find_tie_clusters(
    sorted_valid: Sequence[GuessInput], margin: float, scored_positions: int = 3
) -> list[list[str]]:
    """Group adjacent guesses within ``margin`` of each cluster's highest member.

    Only clusters of two or more that touch a scoring position are returned, because
    only those can change who gets points.
    """
    clusters: list[list[str]] = []
    start = 0
    while start < len(sorted_valid):
        top = sorted_valid[start].similarity
        end = start + 1
        while (
            end < len(sorted_valid)
            and top - sorted_valid[end].similarity <= margin + _FLOAT_TOLERANCE
        ):
            end += 1
        if end - start >= 2 and start < scored_positions:
            clusters.append([x.guess_id for x in sorted_valid[start:end]])
        start = end
    return clusters


def apply_cluster_orders(
    sorted_valid: Sequence[GuessInput],
    clusters: Sequence[Sequence[str]],
    orders: ClusterOrders,
) -> list[GuessInput]:
    """Reorder each cluster by its LLM ordering.

    A missing, ``None`` or invalid ordering (not exactly the cluster's ids) falls back
    to earliest submission time, as spec §3.7 step 4 requires.
    """
    result = list(sorted_valid)
    by_id = {x.guess_id: x for x in sorted_valid}
    for index, cluster in enumerate(clusters):
        members = [by_id[gid] for gid in cluster]
        order = orders.get(index)
        if order is not None and len(order) == len(cluster) and set(order) == set(cluster):
            reordered = [by_id[gid] for gid in order]
        else:
            reordered = sorted(members, key=lambda x: x.submitted_at)
        first = result.index(members[0])
        result[first : first + len(members)] = reordered
    return result


def score_round(
    guesses: Sequence[GuessInput], rules: ScoringRules, cluster_orders: ClusterOrders
) -> RoundOutcome:
    """Rank the guesses, award points by rank and decide the writer bonus."""
    sorted_valid = sort_valid(guesses)
    clusters = find_tie_clusters(sorted_valid, rules.tie_margin, len(rules.points_by_rank))
    ranked = apply_cluster_orders(sorted_valid, clusters, cluster_orders)

    placements = [
        Placement(
            guess_id=x.guess_id,
            user_id=x.user_id,
            rank=position,
            points=rules.points_by_rank[position - 1]
            if position <= len(rules.points_by_rank)
            else 0,
            similarity=x.similarity,
            moderated_out=False,
        )
        for position, x in enumerate(ranked, start=1)
    ]
    placements += [
        Placement(x.guess_id, x.user_id, None, 0, x.similarity, True)
        for x in guesses
        if x.moderated_out
    ]

    best = max((x.similarity for x in sorted_valid), default=None)
    bonus = (
        rules.writer_bonus_points if best is not None and best < rules.writer_bonus_threshold else 0
    )
    return RoundOutcome(placements=placements, writer_bonus=bonus, best_similarity=best)
