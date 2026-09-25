"""Block Kit builders (spec.md §6.1). Pure functions returning ``(text_fallback, blocks)``.

Every action_id comes from ``slack/ids.py``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from jargonator.db.records import GameRecord, PlayerRecord
from jargonator.domain.standings import Highlights, Standing
from jargonator.domain.state import GameState, JargonLevel, PlayerStatus
from jargonator.slack import ids
from jargonator.slack.gateway import Block

if TYPE_CHECKING:
    from jargonator.engine.game_engine import StatusView

Message = tuple[str, list[Block]]

_STATUS_SUFFIX = {
    PlayerStatus.ACTIVE: "",
    PlayerStatus.INACTIVE: " — inactive",
    PlayerStatus.LEFT: " — left",
}


LEVEL_BADGES = {
    JargonLevel.MILD: "🌶️ Mild",
    JargonLevel.SPICY: "🌶️🌶️ Spicy",
    JargonLevel.UNHINGED: "🌶️🌶️🌶️ Unhinged",
}


def escape(text: str) -> str:
    """Escape player text for Slack mrkdwn, so ``<!channel>`` or ``<@U1>`` can't ping."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    ended = game.state is GameState.ENDED
    if ended:
        status = "Game over. Thanks for playing!"
    else:
        status = "Game in progress. Jump in any time!" if started else "Waiting for players…"
    buttons = [] if ended else [_button("🙋 Join", ids.JOIN, game.id, style="primary")]
    if not started and not ended:
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
    ]
    if buttons:
        blocks.append({"type": "actions", "elements": buttons})
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
    text = f"✅ Got it: “{escape(sentence)}”. Turning it into jargon…"
    return text, [_section(text)]


# --- guessing (M4, M5) ------------------------------------------------------------------------


def jargon_out(
    round_no: int, writer_id: str, guessed: int, total: int, deadline: datetime
) -> Message:
    """M4: replaces M2 once the jargon is out. Never shows the jargon itself."""
    text = (
        f"📨 Round {round_no}: {mention(writer_id)}'s jargon is out. "
        "Guessers, check your DMs and guess there, not in this channel! "
        f"({guessed}/{total} guessed)"
    )
    return text, [
        _section(
            f"📨 *Round {round_no}*: {mention(writer_id)}'s jargon is out. "
            "Guessers, check your DMs and guess there, *not in this channel*!"
        ),
        _context(f"🗳️ *{guessed}/{total} guessed* · closes {deadline_text(deadline)}"),
    ]


def guessing_closed(round_no: int, writer_id: str, guessed: int, total: int) -> Message:
    text = f"⚖️ Round {round_no}: guessing closed ({guessed}/{total} guessed). Judging…"
    return text, [
        _section(f"⚖️ *Round {round_no}*: guessing closed. Judging…"),
        _context(f"🗳️ {guessed}/{total} guessed · jargon by {mention(writer_id)}"),
    ]


def _jargon_quote(level: JargonLevel, jargon: str) -> list[Block]:
    return [
        _context(f"*Level:* {LEVEL_BADGES[level]}"),
        _section(f"> {escape(jargon)}"),
    ]


def guess_prompt(
    round_no: int, level: JargonLevel, jargon: str, deadline: datetime, round_id: str
) -> Message:
    """M5: the jargon, DMed to each guesser, with a Submit guess button."""
    text = f"🕵️ Round {round_no}: decode this corporate jargon!"
    return text, [
        _section(f"🕵️ *Round {round_no}: what did they actually say?*"),
        *_jargon_quote(level, jargon),
        {
            "type": "actions",
            "elements": [_button("Submit guess", ids.SUBMIT_GUESS, round_id, style="primary")],
        },
        _context(f"⏱️ One guess, closes {deadline_text(deadline)}"),
    ]


def guess_prompt_submitted(round_no: int, level: JargonLevel, jargon: str, guess: str) -> Message:
    text = f"✅ Round {round_no}: your guess is in."
    return text, [
        _section(f"🕵️ *Round {round_no}*"),
        *_jargon_quote(level, jargon),
        _section(f"✅ *Your guess:* {escape(guess)}"),
    ]


def guess_prompt_closed(
    round_no: int, level: JargonLevel, jargon: str, guess: str | None, channel_id: str
) -> Message:
    """M5 after the round: points the guesser to the results in the channel."""
    text = f"🏁 Round {round_no}: results are in 👉 <#{channel_id}>"
    lines = [_section(f"🏁 *Round {round_no}*: results are in 👉 <#{channel_id}>")]
    lines += _jargon_quote(level, jargon)
    lines.append(
        _context(f"Your guess: {escape(guess)}" if guess else "You didn't guess this time.")
    )
    return text, lines


def round_finished(round_no: int, writer_id: str) -> Message:
    """M2 once the round has results."""
    text = f"🏁 Round {round_no} ({mention(writer_id)}'s sentence) is done. Results below 👇"
    return text, [_section(text)]


# --- results (M6) -----------------------------------------------------------------------------

MEDALS = {1: "🥇", 2: "🥈", 3: "🥉"}
INLINE_OTHER_GUESSES = 5
Controls = Literal["host", "claim", "none"]


@dataclass(frozen=True)
class ResultLine:
    user_id: str
    text: str
    score: int | None
    points: int
    rank: int | None
    moderated: bool


def _guess_line(line: ResultLine) -> str:
    if line.moderated:
        return f"• {mention(line.user_id)}: 🚫 [hidden by moderation]"
    score = f" · {line.score}/100" if line.score is not None else ""
    return f"• {mention(line.user_id)}: “{escape(line.text)}”{score}"


def _leaderboard(standings: Sequence[Standing]) -> str:
    rows = [
        f"{s.rank}. {mention(s.player.user_id)}: {plural(s.player.score, 'pt')}"
        f"{_STATUS_SUFFIX[s.player.status]}"
        for s in standings
    ]
    return "*Leaderboard*\n" + "\n".join(rows)


def _controls(game_id: str, round_id: str, controls: Controls) -> list[Block]:
    if controls == "none":
        return []
    buttons = [
        _button("▶️ Next round", ids.NEXT, game_id, style="primary"),
        _button("🛑 End game", ids.END, game_id, style="danger"),
    ]
    if controls == "claim":
        buttons.append(_button("🙋 Claim host", ids.CLAIM_HOST, round_id))
    return [
        {"type": "actions", "elements": buttons},
        _context("Only the host can start the next round or end the game."),
    ]


def results(
    *,
    game_id: str,
    round_id: str,
    round_no: int,
    writer_id: str,
    level: JargonLevel,
    jargon: str,
    sentence: str,
    lines: Sequence[ResultLine],
    writer_bonus_points: int,
    quip: str | None,
    standings: Sequence[Standing],
    controls: Controls,
) -> tuple[str, list[Block], list[Block] | None]:
    """M6: round results (spec §3.8). Returns (text, blocks, thread_blocks_or_None).

    ``lines`` holds the ranked guesses in order, then the moderated ones.
    """
    top = [line for line in lines if line.rank is not None and line.rank in MEDALS]
    others = [line for line in lines if line not in top]
    text = f"🏁 Round {round_no} results: the original was “{escape(sentence)}”"

    out: list[Block] = [
        _section(f"🏁 *Round {round_no} results* · {LEVEL_BADGES[level]}"),
        _section(f"> {escape(jargon)}"),
        _section(f"*The original:* “{escape(sentence)}”, written by {mention(writer_id)}"),
    ]
    if not lines:
        out.append(_section("_No guesses this round._"))
    if top:
        out.append(
            _section(
                "\n".join(
                    f"{MEDALS[line.rank or 0]} {mention(line.user_id)}: “{escape(line.text)}”"
                    f" · {line.score}/100 · *+{line.points}*"
                    for line in top
                )
            )
        )
    if writer_bonus_points:
        out.append(
            _section(f"🕵️ Nobody cracked it! {mention(writer_id)} earns +{writer_bonus_points}.")
        )
    thread: list[Block] | None = None
    if others:
        if len(others) <= INLINE_OTHER_GUESSES:
            out.append(_section("*Other guesses*\n" + "\n".join(map(_guess_line, others))))
        else:
            out.append(_context(f"🧵 {plural(len(others), 'more guess')} in the thread."))
            thread = [_section("*Other guesses*\n" + "\n".join(map(_guess_line, others)))]
    if quip:
        out.append(_context(f"_{escape(quip)}_"))
    out.append({"type": "divider"})
    out.append(_section(_leaderboard(standings)))
    out += _controls(game_id, round_id, controls)
    return text, out, thread


def failed_round_reveal(
    round_no: int, sentence: str, jargon: str, guesses: Sequence[tuple[str, str]]
) -> Message:
    """Reveal for a round that couldn't be judged (spec §3.11): no points, no scores."""
    text = f"Round {round_no} couldn't be scored. The original was “{escape(sentence)}”"
    guess_lines = "\n".join(f"• {mention(u)}: “{escape(g)}”" for u, g in guesses)
    return text, [
        _section(f"🤷 *Round {round_no} couldn't be scored.*"),
        _section(f"> {escape(jargon)}"),
        _section(f"*The original:* “{escape(sentence)}”"),
        _section("*Guesses*\n" + (guess_lines or "_none_")),
    ]


# --- continuing and ending (M7, M8) ------------------------------------------------------------

END_REASONS = {
    "host": "The host ended the game.",
    "admin": "A workspace admin ended the game.",
    "idle": "💤 The game ended after 2 hours of inactivity.",
    "llm_failure": "The game ended because the AI service stopped responding.",
}


def paused_notice(game_id: str) -> Message:
    text = (
        "⏸️ Not enough players to continue. Click Join to jump in, then the host can "
        "click Next round."
    )
    return text, [_section(text), *_controls(game_id, "", "host")]


def skipped_round(
    round_no: int, game_id: str, round_id: str, reason: str, controls: Controls
) -> Message:
    """Results-lite for a skipped round: the reason plus the host's Next/End controls."""
    return reason, [
        _section(f"⏭️ *Round {round_no} skipped.* {reason}"),
        *_controls(game_id, round_id, controls),
    ]


def inactive_dm() -> Message:
    text = (
        "💤 You missed your turn 3 times in a row, so you've been marked inactive. "
        "Click Join on the game card to come back (your score is kept)."
    )
    return text, [_section(text)]


def host_changed_notice(user_id: str) -> Message:
    return notice(f"👑 {mention(user_id)} is now the host.")


def round_ended_early(round_no: int) -> Message:
    text = f"🛑 The game ended before round {round_no} finished."
    return text, [_section(text)]


def final_scoreboard(
    standings: Sequence[Standing], highlights: Highlights, rounds_played: int, end_reason: str
) -> Message:
    """M7 (spec §3.9): ranks share medals on ties ("1, 1, 3")."""
    played = f"{plural(rounds_played, 'round')} played"
    rows = []
    for s in standings:
        place = MEDALS.get(s.rank, f"{s.rank}.")
        wins = f", {plural(s.player.round_wins, 'round win')}" if s.player.round_wins else ""
        rows.append(
            f"{place} {mention(s.player.user_id)}: *{plural(s.player.score, 'pt')}*{wins}"
            f"{_STATUS_SUFFIX[s.player.status]}"
        )
    out: list[Block] = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": "🏆 Final scoreboard", "emoji": True},
        },
        _context(f"{END_REASONS.get(end_reason, 'The game ended.')} {played}."),
        _section("\n".join(rows) or "_Nobody scored._"),
    ]
    facts = []
    if highlights.best_guess:
        user, guess, score = highlights.best_guess
        facts.append(f"🎯 *Best guess:* {mention(user)}, “{escape(guess)}” ({score}/100)")
    if highlights.most_unhinged:
        writer, jargon = highlights.most_unhinged
        facts.append(
            f"🌀 *Most unhinged jargon* (from {mention(writer)}'s sentence): _{escape(jargon)}_"
        )
    if highlights.top_stumper:
        writer, count = highlights.top_stumper
        facts.append(
            f"🕵️ *Master of mystery:* {mention(writer)} stumped the table {plural(count, 'time')}"
        )
    if facts:
        out += [{"type": "divider"}, _section("*Highlights*\n" + "\n".join(facts))]
    return "🏆 Final scoreboard", out


# --- /jargonator help and status ------------------------------------------------------------

COMMANDS = [
    ("start", "Start a game in this channel"),
    ("join", "Join the game here (any time)"),
    ("leave", "Leave the game (your score is kept)"),
    ("status", "Show the game's state and scores"),
    ("next", "Host: start the next round"),
    ("kick @someone", "Host: remove a player"),
    ("end", "Host or admin: end the game"),
    ("help", "Show this help"),
]


def help_text() -> Message:
    rules = (
        "*How to play:* each round one player DMs me a simple sentence about themselves. "
        "I turn it into corporate jargon and DM it to everyone else, who have "
        "60 seconds to guess the original. An AI judge scores each guess 0–100 on meaning: "
        "the top three get *10 / 5 / 1* points. If nobody gets close, the writer earns *+10*.\n"
        "The jargon ramps up: rounds 1–3 🌶️ Mild, 4–8 🌶️🌶️ Spicy, 9–13 🌶️🌶️🌶️ Unhinged, "
        "then it starts over."
    )
    commands = "\n".join(f"`/jargonator {cmd}`: {desc}" for cmd, desc in COMMANDS)
    return "Jargonator help", [
        {"type": "header", "text": {"type": "plain_text", "text": "💼 Jargonator", "emoji": True}},
        _section(rules),
        _section(commands),
    ]


_STATE_LABELS = {
    GameState.LOBBY: "Lobby: waiting for players",
    GameState.AWAITING_SENTENCE: "is writing",
    GameState.GENERATING: "the AI is jargonizing…",
    GameState.GUESSING: "guessing",
    GameState.JUDGING: "judging…",
    GameState.AWAITING_NEXT: "Waiting for the host to start the next round",
    GameState.PAUSED_PLAYERS: "Paused: waiting for more players",
}


def status(view: "StatusView", now: datetime) -> Message:
    game = view.game
    label = _STATE_LABELS.get(game.state, str(game.state))
    left = (
        f" ({max(0, round((view.deadline - now).total_seconds()))} s left)" if view.deadline else ""
    )
    if view.round_number is not None and game.state in (
        GameState.AWAITING_SENTENCE,
        GameState.GENERATING,
        GameState.GUESSING,
        GameState.JUDGING,
    ):
        writer = mention(view.writer_id) if view.writer_id else "someone"
        subject = f"{writer} {label}" if game.state is GameState.AWAITING_SENTENCE else label
        line = f"*Round {view.round_number}*: {subject}{left}"
    else:
        line = f"*{label}*"
    ranked = sorted(view.players, key=lambda p: (-p.score, p.joined_at))
    scores = "\n".join(
        f"• {mention(p.user_id)}: {plural(p.score, 'pt')}{_STATUS_SUFFIX[p.status]}" for p in ranked
    )
    out = [
        _section(f"💼 {line}\n*Host:* {mention(game.host_user_id)}"),
        _section(f"*Scores*\n{scores}"),
    ]
    if view.upcoming_writers:
        out.append(_context("Up next: " + ", ".join(mention(u) for u in view.upcoming_writers)))
    return f"Jargonator: {line}", out


def llm_retry_notice(seconds: int) -> Message:
    return notice(f"⏳ The AI service is having a hiccup, retrying in {seconds} seconds…")


def llm_failure_notice() -> Message:
    return notice("⚠️ The game can't continue: the AI service isn't responding. Final scores below.")


def notice(text: str) -> Message:
    """M8: a short one-line notice (skipped turn, host change, player joined, …)."""
    return text, [_context(text)]
