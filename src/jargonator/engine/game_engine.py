"""The game engine: every state change goes through here (spec.md §3, §5).

Concurrency model: each game has an ``asyncio.Lock``. Public methods take the lock,
re-read state from the repo, validate, and act. Slow LLM calls run outside the lock, and
the lock is re-taken and the state re-validated before their results are applied, so
duplicate clicks, racing timers and late LLM replies are harmless.
"""

import asyncio
import random
from collections.abc import Sequence
from datetime import datetime, timedelta

import structlog

from jargonator.clock import Clock
from jargonator.config import Settings
from jargonator.db.records import GameRecord
from jargonator.db.repo import ChannelBusyError, RepoProtocol, UserInOtherGameError
from jargonator.domain.state import GameEvent, GameState, PlayerStatus, RoundStatus, transition
from jargonator.domain.turn_order import TurnOrder
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import Scheduler, TimerKind
from jargonator.llm.tasks import LLMTasks
from jargonator.slack import blocks
from jargonator.slack.gateway import Block, MessageRef, SlackDeliveryError, SlackGateway

log = structlog.get_logger()

MIN_SENTENCE, MAX_SENTENCE = 5, 150
NOT_IN_CHANNEL_CODES = frozenset({"not_in_channel", "channel_not_found", "is_archived"})
INVITE_HINT = "I can't post here yet. Invite me with `/invite @Jargonator` first, then try again."


class GameEngine:
    def __init__(
        self,
        *,
        repo: RepoProtocol,
        slack: SlackGateway,
        llm: LLMTasks,
        clock: Clock,
        scheduler: Scheduler,
        settings: Settings,
        rng: random.Random,
    ) -> None:
        self.repo = repo
        self.slack = slack
        self.llm = llm
        self.clock = clock
        self.scheduler = scheduler
        self.settings = settings
        self.rng = rng
        self._locks: dict[str, asyncio.Lock] = {}

    # ===================================================================================
    # Lobby: create / join / leave (spec §3.1, §3.2, §3.10)
    # ===================================================================================

    async def create_game(
        self,
        *,
        channel_id: str,
        user_id: str,
        guess_seconds: int,
        writer_seconds: int,
        join_window_seconds: int,
    ) -> GameRecord:
        now = self.clock.now()
        lobby_deadline = (
            now + timedelta(seconds=join_window_seconds) if join_window_seconds > 0 else None
        )
        try:
            game = await self.repo.create_game(
                channel_id=channel_id,
                host_user_id=user_id,
                guess_seconds=guess_seconds,
                writer_seconds=writer_seconds,
                join_window_seconds=join_window_seconds,
                now=now,
                lobby_deadline=lobby_deadline,
                idle_deadline=self._idle_deadline(now),
            )
        except UserInOtherGameError as exc:
            raise UserFacingError(_in_other_game(exc.game)) from exc
        except ChannelBusyError as exc:
            raise UserFacingError(
                "There's already a game running in this channel. Join it with the Join button."
            ) from exc

        async with self._lock(game.id):
            players = await self.repo.get_players(game.id)
            text, lobby_blocks = blocks.lobby(game, players, started=False)
            try:
                ref = await self.slack.post_message(channel_id, text, lobby_blocks)
            except SlackDeliveryError as exc:
                # Free the channel and the host again. The row is kept (data is never deleted).
                await self.repo.update_game(
                    game.id, state=GameState.ENDED, ended_at=now, end_reason="setup_failed"
                )
                if exc.code in NOT_IN_CHANNEL_CODES:
                    raise UserFacingError(INVITE_HINT) from exc
                raise
            game = await self.repo.update_game(game.id, lobby_message_ts=ref.ts)
            if lobby_deadline is not None:
                self.scheduler.schedule(game.id, TimerKind.LOBBY, lobby_deadline, None)
            self.scheduler.schedule(game.id, TimerKind.IDLE, self._idle_deadline(now), None)
            log.info("game_created", game_id=game.id, channel_id=channel_id, user_id=user_id)
            return game

    async def join(self, channel_id: str, user_id: str) -> None:
        game = await self._require_channel_game(channel_id)
        async with self._lock(game.id):
            game = await self._reload(game.id)
            existing = await self.repo.get_player(game.id, user_id)
            if existing is not None and existing.status is PlayerStatus.ACTIVE:
                raise UserFacingError("You're already in this game 👍")
            try:
                await self.repo.join_game(game.id, user_id, self.clock.now())
            except UserInOtherGameError as exc:
                raise UserFacingError(_in_other_game(exc.game)) from exc
            if game.state is not GameState.LOBBY:
                turn_order = await self.repo.load_turn_order(game.id)
                if turn_order is not None:
                    turn_order.append(user_id)
                    await self.repo.save_turn_order(game.id, turn_order)
                await self._notice(game, f"👋 {blocks.mention(user_id)} joined the game.")
            await self._refresh_lobby(game)
            await self._touch(game.id)
            log.info("player_joined", game_id=game.id, user_id=user_id)

    async def leave(self, channel_id: str, user_id: str) -> None:
        game = await self._require_channel_game(channel_id)
        async with self._lock(game.id):
            game = await self._reload(game.id)
            player = await self.repo.get_player(game.id, user_id)
            if player is None or player.status is PlayerStatus.LEFT:
                raise UserFacingError("You're not in this game.")
            await self.repo.update_player(
                game.id, user_id, status=PlayerStatus.LEFT, left_at=self.clock.now()
            )
            await self._notice(game, f"🚪 {blocks.mention(user_id)} left the game.")
            game = await self._transfer_host_if_needed(game)
            await self._refresh_lobby(game)
            await self._touch(game.id)
            log.info("player_left", game_id=game.id, user_id=user_id)

    # ===================================================================================
    # Starting the game and the writer phase (spec §3.1, §3.3, §3.4)
    # ===================================================================================

    async def start_game(self, channel_id: str, user_id: str) -> None:
        game = await self._require_channel_game(channel_id)
        async with self._lock(game.id):
            game = await self._reload(game.id)
            if game.state is not GameState.LOBBY:
                raise UserFacingError("The game has already started.")
            self._require_host(game, user_id)
            active = await self._active_ids(game.id)
            if len(active) < self.settings.min_players:
                raise UserFacingError(
                    f"You need at least {self.settings.min_players} players to start. "
                    "Ask someone to click Join!"
                )
            await self._start(game, active)

    async def on_lobby_deadline(self, game_id: str) -> None:
        async with self._lock(game_id):
            game = await self.repo.get_game(game_id)
            if game is None or game.state is not GameState.LOBBY:
                return
            active = await self._active_ids(game_id)
            if len(active) >= self.settings.min_players:
                await self._start(game, active)

    async def submit_sentence(self, user_id: str, round_id: str, text: str) -> None:
        """The writer's sentence (spec §3.4). Moderation runs outside the lock."""
        sentence = text.strip()
        rnd = await self.repo.get_round(round_id)
        if rnd is None:
            raise UserFacingError("That round is over.")
        async with self._lock(rnd.game_id):
            try:
                await self._validate_sentence(rnd.game_id, round_id, user_id, sentence)
            except _AlreadySubmitted:
                return  # a double-submit after the first was accepted: ignore quietly

        verdict = await self.llm.moderate_sentence(sentence)

        async with self._lock(rnd.game_id):
            try:
                game = await self._validate_sentence(rnd.game_id, round_id, user_id, sentence)
            except UserFacingError:
                log.info("sentence_discarded_stale", round_id=round_id)
                return  # e.g. a duplicate submission already moved the round on
            if not verdict.ok:
                await self._dm(user_id, *blocks.writer_rejected(verdict.reason, round_id))
                await self._touch(game.id)
                return
            await self.repo.update_round(round_id, sentence=sentence, status=RoundStatus.GENERATING)
            await self.repo.update_player(game.id, user_id, consecutive_misses=0)
            self.scheduler.cancel(game.id, TimerKind.WRITER_REMINDER)
            self.scheduler.cancel(game.id, TimerKind.WRITER_DEADLINE)
            await self._transition(game, GameEvent.SENTENCE_ACCEPTED)
            await self._dm(user_id, *blocks.writer_accepted(sentence))
            await self._touch(game.id)
            log.info("sentence_accepted", game_id=game.id, round_id=round_id)

    async def _validate_sentence(
        self, game_id: str, round_id: str, user_id: str, sentence: str
    ) -> GameRecord:
        game = await self._reload(game_id)
        rnd = await self.repo.get_current_round(game_id)
        if (
            rnd is not None
            and rnd.id == round_id
            and rnd.writer_user_id == user_id
            and rnd.sentence is not None
        ):
            raise _AlreadySubmitted("Your sentence is already in.")
        if (
            rnd is None
            or rnd.id != round_id
            or rnd.status is not RoundStatus.AWAITING_SENTENCE
            or game.state is not GameState.AWAITING_SENTENCE
        ):
            raise UserFacingError("That round is no longer waiting for a sentence.")
        if rnd.writer_user_id != user_id:
            raise UserFacingError("It's not your turn to write.")
        if rnd.writer_deadline is not None and self.clock.now() >= rnd.writer_deadline:
            raise UserFacingError("⏰ Time's up for this round.")
        if not MIN_SENTENCE <= len(sentence) <= MAX_SENTENCE:
            raise UserFacingError(
                f"Your sentence must be {MIN_SENTENCE}–{MAX_SENTENCE} characters long."
            )
        return game

    async def _start(self, game: GameRecord, active: list[str]) -> None:
        now = self.clock.now()
        await self.repo.save_turn_order(game.id, TurnOrder.new(active, self.rng))
        self.scheduler.cancel(game.id, TimerKind.LOBBY)
        game = await self.repo.update_game(game.id, started_at=now, lobby_deadline=None)
        log.info("game_started", game_id=game.id, players=len(active))
        await self._begin_round(game, GameEvent.START)
        await self._refresh_lobby(await self._reload(game.id))  # drops the Start button

    async def _begin_round(self, game: GameRecord, event: GameEvent) -> None:
        """Pick the next writer and open a new round (spec §3.3, §3.4)."""
        now = self.clock.now()
        order = await self.repo.load_turn_order(game.id)
        if order is None:
            raise RuntimeError(f"Game {game.id} has no turn order")
        writer = order.next_writer(await self._active_ids(game.id), self.rng)
        if writer is None:
            raise RuntimeError(f"Game {game.id} has no active players")
        await self.repo.save_turn_order(game.id, order)

        previous = await self.repo.get_current_round(game.id)
        deadline = now + timedelta(seconds=game.writer_seconds)
        reminder_offset = self.settings.writer_reminder_seconds
        reminder = (
            deadline - timedelta(seconds=reminder_offset)
            if reminder_offset < game.writer_seconds
            else None
        )
        rnd = await self.repo.create_round(
            game_id=game.id,
            number=(previous.number + 1) if previous else 1,
            writer_user_id=writer,
            writer_deadline=deadline,
            writer_reminder_at=reminder,
            now=now,
        )
        game = await self._transition(game, event)
        status = await self._post(
            game.channel_id, *blocks.round_start(rnd.number, writer, deadline)
        )
        if status is not None:
            await self.repo.update_round(rnd.id, status_message_ts=status.ts)
        await self._dm(writer, *blocks.writer_prompt(rnd.number, deadline, rnd.id))
        if reminder is not None:
            self.scheduler.schedule(game.id, TimerKind.WRITER_REMINDER, reminder, rnd.id)
        self.scheduler.schedule(game.id, TimerKind.WRITER_DEADLINE, deadline, rnd.id)
        await self._touch(game.id)
        log.info("round_started", game_id=game.id, round_id=rnd.id, number=rnd.number)

    # ===================================================================================
    # Helpers
    # ===================================================================================

    async def _transition(self, game: GameRecord, event: GameEvent) -> GameRecord:
        new_state = transition(game.state, event)
        if new_state is None:
            raise RuntimeError(f"Invalid transition {game.state} --{event}-->")
        return await self.repo.update_game(game.id, state=new_state)

    def _require_host(self, game: GameRecord, user_id: str) -> None:
        if user_id != game.host_user_id:
            raise UserFacingError(
                f"Only the host ({blocks.mention(game.host_user_id)}) can do that."
            )

    async def _active_ids(self, game_id: str) -> list[str]:
        players = await self.repo.get_players(game_id)
        return [p.user_id for p in players if p.status is PlayerStatus.ACTIVE]

    async def _dm(
        self, user_id: str, text: str, message_blocks: Sequence[Block]
    ) -> MessageRef | None:
        try:
            channel = await self.slack.open_dm(user_id)
        except SlackDeliveryError as exc:
            log.warning("slack_dm_failed", user_id=user_id, code=exc.code)
            return None
        return await self._post(channel, text, message_blocks)

    def _lock(self, game_id: str) -> asyncio.Lock:
        return self._locks.setdefault(game_id, asyncio.Lock())

    def _idle_deadline(self, now: datetime) -> datetime:
        return now + timedelta(seconds=self.settings.idle_timeout_seconds)

    async def _reload(self, game_id: str) -> GameRecord:
        game = await self.repo.get_game(game_id)
        if game is None:
            raise LookupError(game_id)
        if game.state is GameState.ENDED:
            raise UserFacingError("That game has already ended.")
        return game

    async def _require_channel_game(self, channel_id: str) -> GameRecord:
        game = await self.repo.get_active_game_by_channel(channel_id)
        if game is None:
            raise UserFacingError("No game running here. Start one with `/jargonator start`.")
        return game

    async def _touch(self, game_id: str) -> None:
        """Record activity: push the idle deadline out (spec §3.9, §9)."""
        now = self.clock.now()
        deadline = self._idle_deadline(now)
        await self.repo.update_game(game_id, last_activity_at=now, idle_deadline=deadline)
        self.scheduler.schedule(game_id, TimerKind.IDLE, deadline, None)

    async def _transfer_host_if_needed(self, game: GameRecord) -> GameRecord:
        """If the host is no longer active, pass hosting to the longest-standing active
        player (spec §3.10). Keeps the host when nobody else is active."""
        players = await self.repo.get_players(game.id)
        host = next((p for p in players if p.user_id == game.host_user_id), None)
        if host is not None and host.status is PlayerStatus.ACTIVE:
            return game
        candidates = [
            p for p in players if p.status is PlayerStatus.ACTIVE and p.user_id != game.host_user_id
        ]
        if not candidates:
            return game
        new_host = candidates[0]  # get_players is ordered by joined_at
        game = await self.repo.update_game(game.id, host_user_id=new_host.user_id)
        await self._notice(game, f"👑 {blocks.mention(new_host.user_id)} is now the host.")
        log.info("host_transferred", game_id=game.id, user_id=new_host.user_id)
        return game

    async def _refresh_lobby(self, game: GameRecord) -> None:
        if game.lobby_message_ts is None:
            return
        players = await self.repo.get_players(game.id)
        text, lobby_blocks = blocks.lobby(game, players, started=game.state is not GameState.LOBBY)
        await self._update(MessageRef(game.channel_id, game.lobby_message_ts), text, lobby_blocks)

    async def _notice(self, game: GameRecord, text: str) -> MessageRef | None:
        notice_text, notice_blocks = blocks.notice(text)
        return await self._post(game.channel_id, notice_text, notice_blocks)

    # --- Slack calls that must never break a state transition ------------------------------

    async def _post(
        self,
        channel: str,
        text: str,
        message_blocks: Sequence[Block],
        thread_ts: str | None = None,
    ) -> MessageRef | None:
        try:
            return await self.slack.post_message(channel, text, message_blocks, thread_ts)
        except SlackDeliveryError as exc:
            log.warning("slack_post_failed", channel_id=channel, code=exc.code)
            return None

    async def _update(self, ref: MessageRef, text: str, message_blocks: Sequence[Block]) -> None:
        try:
            await self.slack.update_message(ref, text, message_blocks)
        except SlackDeliveryError as exc:
            log.warning("slack_update_failed", channel_id=ref.channel, code=exc.code)


class _AlreadySubmitted(UserFacingError):
    """The writer's sentence for this round was already accepted."""


def _in_other_game(game: GameRecord) -> str:
    return (
        f"You're already in a game in <#{game.channel_id}>. "
        "Leave it first with `/jargonator leave` there."
    )
