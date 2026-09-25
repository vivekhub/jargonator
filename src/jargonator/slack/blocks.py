"""Block Kit builders (spec.md §6.1). Pure functions returning ``(text_fallback, blocks)``.

Every action_id comes from ``slack/ids.py``.
"""

from collections.abc import Sequence

from jargonator.db.records import GameRecord, PlayerRecord
from jargonator.domain.state import PlayerStatus
from jargonator.slack import ids
from jargonator.slack.gateway import Block

Message = tuple[str, list[Block]]

_STATUS_SUFFIX = {
    PlayerStatus.ACTIVE: "",
    PlayerStatus.INACTIVE: " — inactive",
    PlayerStatus.LEFT: " — left",
}


def plural(count: int, noun: str) -> str:
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def mention(user_id: str) -> str:
    return f"<@{user_id}>"


def _section(text: str) -> Block:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _context(text: str) -> Block:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": text}]}


def _button(text: str, action_id: str, value: str, style: str | None = None) -> Block:
    button: Block = {
        "type": "button",
        "text": {"type": "plain_text", "text": text, "emoji": True},
        "action_id": action_id,
        "value": value,
    }
    if style:
        button["style"] = style
    return button


def _player_line(player: PlayerRecord, host_user_id: str) -> str:
    crown = "👑 " if player.user_id == host_user_id else ""
    return f"• {crown}{mention(player.user_id)}{_STATUS_SUFFIX[player.status]}"


def lobby(game: GameRecord, players: Sequence[PlayerRecord], started: bool) -> Message:
    """M1: the game card. Join stays for the whole game; Start only until the game starts."""
    active = [p for p in players if p.status is PlayerStatus.ACTIVE]
    status = "Game in progress. Jump in any time!" if started else "Waiting for players…"
    buttons = [_button("🙋 Join", ids.JOIN, game.id, style="primary")]
    if not started:
        buttons.append(_button("🚀 Start game", ids.START, game.id))

    blocks: list[Block] = [
        {"type": "header", "text": {"type": "plain_text", "text": "💼 Jargonator", "emoji": True}},
        _section(
            "Decode your colleagues' corporate jargon! Each round one player writes a simple "
            "sentence about themselves, the AI buzzword-ifies it, and everyone else guesses "
            f"the original.\n*Host:* {mention(game.host_user_id)} · {status}"
        ),
        _context(f"⏱️ Guess time {game.guess_seconds} s · ✍️ Writer time {game.writer_seconds} s"),
        _section(
            f"*Players ({len(active)})*\n"
            + "\n".join(_player_line(p, game.host_user_id) for p in players)
        ),
        {"type": "actions", "elements": buttons},
    ]
    text = f"💼 Jargonator: {plural(len(active), 'player')}. {status}"
    return text, blocks


def notice(text: str) -> Message:
    """M8: a short one-line notice (skipped turn, host change, player joined, …)."""
    return text, [_context(text)]
