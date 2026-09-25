"""Block Kit builders (spec.md §6.1). Pure functions returning ``(text_fallback, blocks)``.

Every action_id comes from ``slack/ids.py``.
"""

from collections.abc import Sequence
from datetime import datetime

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


def deadline_text(when: datetime) -> str:
    """Slack renders this in each viewer's local time. The fallback is UTC."""
    return f"<!date^{int(when.timestamp())}^{{time_secs}}|{when:%H:%M:%S} UTC>"


# --- round start and the writer's DMs (M2, M3) ----------------------------------------------


def round_start(round_no: int, writer_id: str, writer_deadline: datetime) -> Message:
    """M2: the channel status message for a round (later updated in place)."""
    text = f"🎤 Round {round_no}: {mention(writer_id)} is writing a sentence…"
    return text, [
        _section(f"🎤 *Round {round_no}*: {mention(writer_id)} is writing a sentence…"),
        _context(f"✍️ Sentence due by {deadline_text(writer_deadline)}"),
    ]


def writer_prompt(round_no: int, deadline: datetime, round_id: str) -> Message:
    """M3: DM asking the writer for their sentence."""
    text = f"✍️ It's your turn to write (round {round_no})!"
    return text, [
        _section(
            f"✍️ *It's your turn to write (round {round_no})!*\n"
            "Write one *simple, true sentence about yourself*. The AI will turn it into "
            "corporate jargon, and everyone else will try to guess the original.\n"
            "_Example: I ran a marathon last year._"
        ),
        {
            "type": "actions",
            "elements": [_button("Write sentence", ids.WRITE_SENTENCE, round_id, style="primary")],
        },
        _context(f"⏱️ Due by {deadline_text(deadline)}. Keep it about you, and keep it friendly."),
    ]


def writer_rejected(reason: str, round_id: str) -> Message:
    text = f"🙅 That sentence can't be used: {reason}"
    return text, [
        _section(f"🙅 *That sentence can't be used.*\n{reason}"),
        {"type": "actions", "elements": [_button("Try again", ids.TRY_AGAIN, round_id)]},
        _context("The clock is still running."),
    ]


def writer_reminder(seconds_left: int, round_id: str) -> Message:
    text = f"⏰ {seconds_left} seconds left to write your sentence!"
    return text, [
        _section(f"⏰ *{seconds_left} seconds left* to write your sentence!"),
        {
            "type": "actions",
            "elements": [_button("Write sentence", ids.WRITE_SENTENCE, round_id, style="primary")],
        },
    ]


def writer_accepted(sentence: str) -> Message:
    text = f"✅ Got it: “{sentence}”. Turning it into jargon…"
    return text, [_section(text)]


def notice(text: str) -> Message:
    """M8: a short one-line notice (skipped turn, host change, player joined, …)."""
    return text, [_context(text)]
