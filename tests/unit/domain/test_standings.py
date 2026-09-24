from jargonator.domain.standings import (
    Highlights,
    PlayerScore,
    RoundSummary,
    compute_highlights,
    rank_players,
)
from jargonator.domain.state import JargonLevel, PlayerStatus, RoundStatus

A = PlayerStatus.ACTIVE


def p(user: str, score: int, wins: int = 0, status: PlayerStatus = A) -> PlayerScore:
    return PlayerScore(user_id=user, score=score, round_wins=wins, status=status)


def r(
    writer: str,
    *,
    level: JargonLevel = JargonLevel.SPICY,
    jargon: str | None = "synergy",
    best: int | None = None,
    best_user: str | None = None,
    best_text: str | None = None,
    bonus: bool = False,
    status: RoundStatus = RoundStatus.COMPLETED,
) -> RoundSummary:
    return RoundSummary(writer, level, jargon, best, best_user, best_text, bonus, status)


# --- rank_players -------------------------------------------------------------


def test_sorted_by_score() -> None:
    out = rank_players([p("a", 5), p("b", 20), p("c", 10)])
    assert [(s.rank, s.player.user_id) for s in out] == [(1, "b"), (2, "c"), (3, "a")]


def test_ties_share_rank_and_skip_next() -> None:
    out = rank_players([p("a", 10), p("b", 10), p("c", 3)])
    assert [(s.rank, s.player.user_id) for s in out] == [(1, "a"), (1, "b"), (3, "c")]


def test_round_wins_break_score_ties() -> None:
    out = rank_players([p("a", 10, wins=0), p("b", 10, wins=1)])
    assert [(s.rank, s.player.user_id) for s in out] == [(1, "b"), (2, "a")]


def test_inactive_and_left_players_included() -> None:
    out = rank_players(
        [p("a", 1, status=PlayerStatus.LEFT), p("b", 2, status=PlayerStatus.INACTIVE)]
    )
    assert {s.player.user_id for s in out} == {"a", "b"}


def test_empty() -> None:
    assert rank_players([]) == []


# --- compute_highlights -----------------------------------------------------------


def test_empty_rounds() -> None:
    assert compute_highlights([]) == Highlights(None, None, None)


def test_best_guess_across_rounds() -> None:
    rounds = [
        r("w1", best=70, best_user="a", best_text="I have cats"),
        r("w2", best=90, best_user="b", best_text="I ran a marathon"),
        r("w3", best=90, best_user="c", best_text="later tie loses"),
    ]
    assert compute_highlights(rounds).best_guess == ("b", "I ran a marathon", 90)


def test_most_unhinged_is_longest_unhinged_jargon() -> None:
    rounds = [
        r("w1", level=JargonLevel.SPICY, jargon="x" * 500),
        r("w2", level=JargonLevel.UNHINGED, jargon="short"),
        r("w3", level=JargonLevel.UNHINGED, jargon="much longer jargon"),
    ]
    assert compute_highlights(rounds).most_unhinged == ("w3", "much longer jargon")


def test_no_unhinged_rounds() -> None:
    assert compute_highlights([r("w1")]).most_unhinged is None


def test_top_stumper_most_bonus_rounds() -> None:
    rounds = [r("a", bonus=True), r("b", bonus=True), r("b", bonus=True), r("a", bonus=False)]
    assert compute_highlights(rounds).top_stumper == ("b", 2)


def test_top_stumper_tie_goes_to_first_to_reach_count() -> None:
    rounds = [r("b", bonus=True), r("a", bonus=True), r("a", bonus=True), r("b", bonus=True)]
    assert compute_highlights(rounds).top_stumper == ("a", 2)


def test_no_stumper_without_bonus_rounds() -> None:
    assert compute_highlights([r("a"), r("b")]).top_stumper is None


def test_only_completed_rounds_count() -> None:
    rounds = [
        r("a", status=RoundStatus.SKIPPED, jargon=None, bonus=True),
        r(
            "b",
            status=RoundStatus.VOIDED,
            level=JargonLevel.UNHINGED,
            jargon="voided jargon",
            best=99,
            best_user="x",
            best_text="t",
        ),
    ]
    assert compute_highlights(rounds) == Highlights(None, None, None)
