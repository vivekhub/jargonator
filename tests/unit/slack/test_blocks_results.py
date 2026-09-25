from typing import Literal

from jargonator.domain.standings import PlayerScore, Standing
from jargonator.domain.state import JargonLevel, PlayerStatus
from jargonator.slack import ids
from jargonator.slack.blocks import ResultLine, failed_round_reveal, guess_prompt_closed, results
from tests.snapshot import assert_snapshot

A = PlayerStatus.ACTIVE
STANDINGS = [
    Standing(1, PlayerScore("U2", 25, 2, A)),
    Standing(2, PlayerScore("U3", 10, 1, A)),
    Standing(3, PlayerScore("U4", 1, 0, PlayerStatus.LEFT)),
    Standing(4, PlayerScore("U1", 0, 0, A)),
]
TOP = [
    ResultLine("U2", "I own a pair of kitties", 95, 10, 1, False),
    ResultLine("U3", "I have a cat", 80, 5, 2, False),
    ResultLine("U4", "I own pets", 55, 1, 3, False),
]


def render(
    lines: list[ResultLine],
    *,
    bonus: int = 0,
    quip: str | None = "Purr-formance review: exceeds expectations.",
    controls: Literal["host", "claim", "none"] = "host",
) -> tuple[str, list[dict[str, object]], list[dict[str, object]] | None]:
    return results(
        game_id="g1",
        round_id="r1",
        round_no=2,
        writer_id="U1",
        level=JargonLevel.SPICY,
        jargon="I steward a dual-asset feline stakeholder portfolio.",
        sentence="I have two cats",
        lines=lines,
        writer_bonus_points=bonus,
        quip=quip,
        standings=STANDINGS,
        controls=controls,
    )


def action_ids(blocks: list[dict[str, object]]) -> list[str]:
    return [
        e["action_id"]  # type: ignore[index]
        for b in blocks
        if b["type"] == "actions"
        for e in b["elements"]  # type: ignore[attr-defined]
    ]


def test_normal_results_snapshot() -> None:
    text, blocks, overflow = render(TOP)
    assert_snapshot("results_normal", {"text": text, "blocks": blocks})
    assert overflow is None
    assert "I have two cats" in text
    assert action_ids(blocks) == [ids.NEXT, ids.END]
    rendered = str(blocks)
    for expected in ("🥇", "95/100", "+10", "🥈", "🥉", "Leaderboard", "— left", "Purr-formance"):
        assert expected in rendered


def test_writer_bonus_snapshot() -> None:
    low = [ResultLine("U2", "I like trains", 20, 10, 1, False)]
    text, blocks, _ = render(low, bonus=10)
    assert_snapshot("results_bonus", {"text": text, "blocks": blocks})
    assert "Nobody cracked it" in str(blocks) and "<@U1> earns +10" in str(blocks)


def test_moderated_snapshot() -> None:
    lines = [*TOP, ResultLine("U5", "something rude", None, 0, None, True)]
    text, blocks, _ = render(lines)
    assert_snapshot("results_moderated", {"text": text, "blocks": blocks})
    assert "something rude" not in str(blocks)
    assert "🚫 [hidden by moderation]" in str(blocks)


def test_overflow_goes_to_thread() -> None:
    others = [ResultLine(f"U{i}", f"guess {i}", 30 - i, 0, i, False) for i in range(4, 11)]
    text, blocks, overflow = render([*TOP, *others])
    assert_snapshot("results_overflow", {"text": text, "blocks": blocks, "thread": overflow})
    assert overflow is not None and "guess 10" in str(overflow)
    assert "guess 10" not in str(blocks) and "in the thread" in str(blocks)


def test_no_guesses_snapshot() -> None:
    text, blocks, _ = render([], quip=None)
    assert_snapshot("results_no_guesses", {"text": text, "blocks": blocks})
    assert "No guesses this round" in str(blocks)


def test_claim_host_controls() -> None:
    text, blocks, _ = render(TOP, controls="claim")
    assert_snapshot("results_claim_host", {"text": text, "blocks": blocks})
    assert action_ids(blocks) == [ids.NEXT, ids.END, ids.CLAIM_HOST]


def test_no_controls() -> None:
    _, blocks, _ = render(TOP, controls="none")
    assert action_ids(blocks) == []


def test_player_text_is_escaped() -> None:
    _, blocks, _ = render([ResultLine("U2", "<!channel> hi", 50, 10, 1, False)])
    assert "<!channel>" not in str(blocks)


def test_failed_round_reveal_snapshot() -> None:
    text, blocks = failed_round_reveal(
        3, "I have two cats", "feline synergy", [("U2", "kitties"), ("U3", "<b>dogs</b>")]
    )
    assert_snapshot("failed_round_reveal", {"text": text, "blocks": blocks})
    assert "I have two cats" in str(blocks) and "&lt;b&gt;dogs" in str(blocks)


def test_guess_prompt_closed() -> None:
    text, blocks = guess_prompt_closed(2, JargonLevel.MILD, "synergy", "cats", "C1")
    assert "<#C1>" in text and "cats" in str(blocks)
