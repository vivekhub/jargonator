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
from jargonator.domain.state import GameState, PlayerStatus
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import Scheduler, TimerKind
from jargonator.llm.tasks import LLMTasks
from jargonator.slack import blocks
from jargonator.slack.gateway import Block, MessageRef, SlackDeliveryError, SlackGateway

log = structlog.get_logger()

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
    # Helpers
    # ===================================================================================

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


def _in_other_game(game: GameRecord) -> str:
    return (
        f"You're already in a game in <#{game.channel_id}>. "
        "Leave it first with `/jargonator leave` there."
    )
