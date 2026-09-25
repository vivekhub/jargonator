from dataclasses import replace

from jargonator.domain.standings import Highlights, PlayerScore, Standing
from jargonator.domain.state import GameState, PlayerStatus
from jargonator.slack import ids
from jargonator.slack.blocks import final_scoreboard, lobby, paused_notice, round_ended_early
from tests.snapshot import assert_snapshot
from tests.unit.slack.test_blocks_lobby import GAME, PLAYERS

A = PlayerStatus.ACTIVE


def test_final_scoreboard_snapshot() -> None:
    standings = [
        Standing(1, PlayerScore("U2", 26, 2, A)),
        Standing(1, PlayerScore("U3", 26, 2, A)),
        Standing(3, PlayerScore("U1", 10, 0, PlayerStatus.LEFT)),
        Standing(4, PlayerScore("U4", 0, 0, PlayerStatus.INACTIVE)),
    ]
    highlights = Highlights(
        best_guess=("U2", "I own a pair of kitties", 98),
        most_unhinged=("U3", "I operationalise a paradigm-shifting caffeine ingestion framework."),
        top_stumper=("U1", 2),
    )
    text, blocks = final_scoreboard(standings, highlights, rounds_played=5, end_reason="host")
    assert_snapshot("final_scoreboard", {"text": text, "blocks": blocks})
    rendered = str(blocks)
    assert "🥇 <@U2>" in rendered and "🥇 <@U3>" in rendered and "3. <@U1>" not in rendered
    assert "5 rounds" in rendered and "kitties" in rendered and "stumped" in rendered
    assert "action_id" not in rendered


def test_final_scoreboard_idle_and_empty() -> None:
    text, blocks = final_scoreboard([], Highlights(None, None, None), 0, "idle")
    assert_snapshot("final_scoreboard_idle", {"text": text, "blocks": blocks})
    assert "inactivity" in str(blocks)


def test_paused_notice_has_controls() -> None:
    text, blocks = paused_notice("g1")
    assert_snapshot("paused_notice", {"text": text, "blocks": blocks})
    assert ids.NEXT in str(blocks) and "Join" in text


def test_round_ended_early() -> None:
    text, _ = round_ended_early(3)
    assert "ended" in text


def test_lobby_after_game_over_has_no_buttons() -> None:
    ended = replace(GAME, state=GameState.ENDED)
    text, blocks = lobby(ended, PLAYERS, started=True)
    assert "action_id" not in str(blocks)
    assert "Game over" in text
