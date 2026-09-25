from datetime import UTC, datetime

from jargonator.domain.state import GameState, RoundStatus
from jargonator.engine.game_engine import StatusView
from jargonator.slack.blocks import help_text, status
from tests.snapshot import assert_snapshot
from tests.unit.slack.test_blocks_lobby import GAME, PLAYERS

NOW = datetime(2026, 3, 1, 9, 31, tzinfo=UTC)


def test_help_snapshot() -> None:
    text, blocks = help_text()
    assert_snapshot("help", {"text": text, "blocks": blocks})
    for command in ("start", "join", "leave", "status", "next", "kick", "end", "help"):
        assert f"/jargonator {command}" in str(blocks)


def test_status_in_lobby() -> None:
    view = StatusView(
        game=GAME,
        players=PLAYERS,
        round_number=None,
        round_status=None,
        writer_id=None,
        deadline=None,
        upcoming_writers=[],
    )
    text, blocks = status(view, NOW)
    assert_snapshot("status_lobby", {"text": text, "blocks": blocks})
    assert "Lobby" in str(blocks)


def test_status_mid_round() -> None:
    game = GAME.__class__(**{**GAME.__dict__, "state": GameState.GUESSING})
    view = StatusView(
        game=game,
        players=PLAYERS,
        round_number=3,
        round_status=RoundStatus.GUESSING,
        writer_id="U2",
        deadline=datetime(2026, 3, 1, 9, 31, 45, tzinfo=UTC),
        upcoming_writers=["U3", "U1"],
    )
    text, blocks = status(view, NOW)
    assert_snapshot("status_guessing", {"text": text, "blocks": blocks})
    rendered = str(blocks)
    assert "Round 3" in rendered and "45 s left" in rendered and "Up next" in rendered
