from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from jargonator.db.records import GameRecord, PlayerRecord
from jargonator.domain.state import GameState, PlayerStatus
from jargonator.slack import ids
from jargonator.slack.blocks import lobby, notice
from tests.snapshot import assert_snapshot

T0 = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)

GAME = GameRecord(
    id="game-1",
    channel_id="C1",
    host_user_id="U1",
    state=GameState.LOBBY,
    created_by="U1",
    guess_seconds=60,
    writer_seconds=90,
    join_window_seconds=120,
    lobby_message_ts="1.0",
    created_at=T0,
    started_at=None,
    ended_at=None,
    end_reason=None,
    lobby_deadline=T0 + timedelta(seconds=120),
    idle_deadline=None,
    last_activity_at=T0,
    last_writer=None,
)


def player(user: str, status: PlayerStatus = PlayerStatus.ACTIVE, score: int = 0) -> PlayerRecord:
    return PlayerRecord("game-1", user, status, score, 0, 0, T0, None)


PLAYERS = [player("U1"), player("U2"), player("U3")]


def walk(blocks: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for block in blocks:
        yield block
        for key in ("elements", "accessory"):
            value = block.get(key)
            if isinstance(value, list):
                yield from walk(value)
            elif isinstance(value, dict):
                yield from walk([value])


def action_ids(blocks: list[dict[str, Any]]) -> list[str]:
    return [b["action_id"] for b in walk(blocks) if "action_id" in b]


def test_lobby_before_start_snapshot() -> None:
    text, blocks = lobby(GAME, PLAYERS, started=False)
    assert_snapshot("lobby_before_start", {"text": text, "blocks": blocks})
    assert action_ids(blocks) == [ids.JOIN, ids.START]


def test_lobby_after_start_has_no_start_button() -> None:
    started = replace(GAME, state=GameState.AWAITING_SENTENCE, started_at=T0)
    text, blocks = lobby(started, PLAYERS, started=True)
    assert_snapshot("lobby_after_start", {"text": text, "blocks": blocks})
    assert action_ids(blocks) == [ids.JOIN]


def test_lobby_marks_left_and_inactive_players() -> None:
    players = [player("U1"), player("U2", PlayerStatus.LEFT), player("U3", PlayerStatus.INACTIVE)]
    text, blocks = lobby(GAME, players, started=False)
    assert_snapshot("lobby_with_left_player", {"text": text, "blocks": blocks})
    rendered = str(blocks)
    assert "<@U2> — left" in rendered and "<@U3> — inactive" in rendered
    assert "👑 <@U1>" in rendered


def test_lobby_buttons_carry_game_id() -> None:
    _, blocks = lobby(GAME, PLAYERS, started=False)
    assert {b["value"] for b in walk(blocks) if "action_id" in b} == {"game-1"}


def test_lobby_text_fallback_mentions_channel_context() -> None:
    text, _ = lobby(GAME, PLAYERS, started=False)
    assert "Jargonator" in text and "3 players" in text


def test_lobby_text_singular_player() -> None:
    text, _ = lobby(GAME, [player("U1")], started=False)
    assert "1 player." in text


def test_notice_is_a_context_block() -> None:
    text, blocks = notice("⏭️ <@U1> didn't submit in time")
    assert text == "⏭️ <@U1> didn't submit in time"
    assert blocks == [
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "⏭️ <@U1> didn't submit in time"}],
        }
    ]


def test_ids_are_unique_and_namespaced() -> None:
    values = [v for k, v in vars(ids).items() if k.isupper()]
    assert len(values) == len(set(values)) >= 11
    assert all(v.startswith("jargonator_") for v in values)
