"""Leaderboard ranking and end-of-game highlights (spec.md §3.8, §3.9)."""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass

from jargonator.domain.state import JargonLevel, PlayerStatus, RoundStatus


@dataclass(frozen=True)
class PlayerScore:
    user_id: str
    score: int
    round_wins: int
    status: PlayerStatus


@dataclass(frozen=True)
class Standing:
    rank: int
    player: PlayerScore


@dataclass(frozen=True)
class RoundSummary:
    writer_id: str
    level: JargonLevel | None
    jargon: str | None
    best_similarity: float | None
    best_guess_user: str | None
    best_guess_text: str | None
    writer_bonus: bool
    status: RoundStatus


@dataclass(frozen=True)
class Highlights:
    best_guess: tuple[str, str, float] | None
    """(user_id, guess text, similarity)"""
    most_unhinged: tuple[str, str] | None
    """(writer_id, jargon)"""
    top_stumper: tuple[str, int] | None
    """(writer_id, number of rounds where nobody cracked their sentence)"""


def rank_players(players: Sequence[PlayerScore]) -> list[Standing]:
    """Sort by score, then round wins, and use competition ranking ("1, 1, 3").

    Players tie only when both score and round wins are equal. User id ordering just
    makes the output deterministic.
    """
    ordered = sorted(players, key=lambda p: (-p.score, -p.round_wins, p.user_id))
    standings: list[Standing] = []
    for position, player in enumerate(ordered, start=1):
        previous = standings[-1] if standings else None
        tied = previous is not None and (previous.player.score, previous.player.round_wins) == (
            player.score,
            player.round_wins,
        )
        standings.append(Standing(previous.rank if tied and previous else position, player))
    return standings


def compute_highlights(rounds: Sequence[RoundSummary]) -> Highlights:
    """Pick the game highlights from completed rounds only. Earlier rounds win ties."""
    completed = [r for r in rounds if r.status is RoundStatus.COMPLETED]

    best_guess: tuple[str, str, float] | None = None
    for r in completed:
        if r.best_similarity is None or r.best_guess_user is None or r.best_guess_text is None:
            continue
        if best_guess is None or r.best_similarity > best_guess[2]:
            best_guess = (r.best_guess_user, r.best_guess_text, r.best_similarity)

    most_unhinged: tuple[str, str] | None = None
    for r in completed:
        if (
            r.level is JargonLevel.UNHINGED
            and r.jargon
            and (most_unhinged is None or len(r.jargon) > len(most_unhinged[1]))
        ):
            most_unhinged = (r.writer_id, r.jargon)

    top_stumper: tuple[str, int] | None = None
    counts: Counter[str] = Counter()
    for r in completed:
        if r.writer_bonus:
            counts[r.writer_id] += 1
            if top_stumper is None or counts[r.writer_id] > top_stumper[1]:
                top_stumper = (r.writer_id, counts[r.writer_id])

    return Highlights(best_guess, most_unhinged, top_stumper)
