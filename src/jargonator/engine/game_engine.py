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
from jargonator.db.records import GameRecord, RoundRecord
from jargonator.db.repo import (
    ChannelBusyError,
    DuplicateGuessError,
    RepoProtocol,
    UserInOtherGameError,
)
from jargonator.domain.state import (
    GameEvent,
    GameState,
    JargonLevel,
    PlayerStatus,
    RoundStatus,
    transition,
)
from jargonator.domain.text import is_leaky, leaked_words
from jargonator.domain.turn_order import TurnOrder
from jargonator.engine.debounce import Debouncer
from jargonator.engine.errors import UserFacingError
from jargonator.engine.scheduler import Scheduler, TimerKind
from jargonator.llm.client import LLMError
from jargonator.llm.tasks import LLMTasks
from jargonator.slack import blocks
from jargonator.slack.gateway import Block, MessageRef, SlackDeliveryError, SlackGateway

log = structlog.get_logger()

MIN_SENTENCE, MAX_SENTENCE = 5, 150
MIN_GUESS, MAX_GUESS = 1, 200
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
        self._counter_updates = Debouncer(clock)

    async def aclose(self) -> None:
        """Cancel background work (debounced Slack updates)."""
        await self._counter_updates.aclose()

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
            close_round = await self._everyone_has_guessed(game)
        if close_round is not None:
            await self._close_guessing(game.id, close_round)

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
        await self._generate_and_distribute(rnd.game_id, round_id)

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
    # Jargon generation and distribution (spec §3.5, §3.6) + essential-LLM rule (§3.11)
    # ===================================================================================

    async def _generate_and_distribute(self, game_id: str, round_id: str) -> None:
        async with self._lock(game_id):
            live = await self._live_round(game_id, round_id, GameState.GENERATING)
            if live is None:
                return
            _, rnd = live
            level = rnd.level
            if level is None:  # keep the same level across the 30 s retry and restarts
                level = self.rng.choice(list(JargonLevel))
                await self.repo.update_round(round_id, level=level)
            sentence = rnd.sentence or ""

        try:
            jargon = await self.llm.generate_jargon(sentence, level)
            if is_leaky(sentence, jargon):
                log.info("jargon_leaky_regenerating", round_id=round_id)
                jargon = await self.llm.generate_jargon(
                    sentence, level, avoid_words=leaked_words(sentence, jargon)
                )
        except LLMError:
            log.warning("jargon_generation_failed", game_id=game_id, round_id=round_id)
            await self._essential_llm_failure(game_id, round_id)
            return

        close_immediately = False
        async with self._lock(game_id):
            live = await self._live_round(game_id, round_id, GameState.GENERATING)
            if live is None:
                return  # e.g. the game ended while the LLM was working
            game, rnd = live
            now = self.clock.now()
            deadline = now + timedelta(seconds=game.guess_seconds)
            await self.repo.update_round(
                round_id,
                jargon=jargon,
                llm_retry_at=None,
                guess_deadline=deadline,
                status=RoundStatus.GUESSING,
            )
            recipients: list[tuple[str, str]] = []
            for user_id in await self._active_ids(game_id):
                if user_id == rnd.writer_user_id:
                    continue
                try:
                    recipients.append((user_id, await self.slack.open_dm(user_id)))
                except SlackDeliveryError as exc:
                    log.warning("guesser_unreachable", user_id=user_id, code=exc.code)
            await self.repo.add_round_guessers(round_id, recipients)
            for user_id, dm_channel in recipients:
                ref = await self._post(
                    dm_channel, *blocks.guess_prompt(rnd.number, level, jargon, deadline, round_id)
                )
                if ref is not None:
                    await self.repo.set_guesser_dm_ts(round_id, user_id, ref.ts)
            game = await self._transition(game, GameEvent.JARGON_READY)
            if rnd.status_message_ts is not None:
                await self._update(
                    MessageRef(game.channel_id, rnd.status_message_ts),
                    *blocks.jargon_out(
                        rnd.number, rnd.writer_user_id, 0, len(recipients), deadline
                    ),
                )
            self.scheduler.schedule(game_id, TimerKind.GUESS_DEADLINE, deadline, round_id)
            log.info(
                "jargon_distributed", game_id=game_id, round_id=round_id, guessers=len(recipients)
            )
            close_immediately = not recipients
        if close_immediately:
            await self._close_guessing(game_id, round_id)

    async def _essential_llm_failure(self, game_id: str, round_id: str) -> None:
        """First failure: announce and retry in LLM_FAILURE_RETRY_SECONDS. Second: end the
        game (spec §3.11). The step's state (GENERATING/JUDGING) is kept while waiting."""
        async with self._lock(game_id):
            game = await self.repo.get_game(game_id)
            rnd = await self.repo.get_round(round_id)
            if (
                game is None
                or rnd is None
                or game.state is GameState.ENDED
                or rnd.status not in (RoundStatus.GENERATING, RoundStatus.JUDGING)
            ):
                return
            if rnd.llm_retry_at is None:
                seconds = self.settings.llm_failure_retry_seconds
                retry_at = self.clock.now() + timedelta(seconds=seconds)
                await self.repo.update_round(round_id, llm_retry_at=retry_at)
                await self._post(game.channel_id, *blocks.llm_retry_notice(seconds))
                self.scheduler.schedule(game_id, TimerKind.LLM_RETRY, retry_at, round_id)
                log.warning("llm_retry_scheduled", game_id=game_id, round_id=round_id)
                return
            await self._end_for_llm_failure(game, round_id)

    async def on_llm_retry(self, game_id: str, round_id: str) -> None:
        rnd = await self.repo.get_round(round_id)
        if rnd is None:
            return
        if rnd.status is RoundStatus.GENERATING:
            await self._generate_and_distribute(game_id, round_id)

    async def _end_for_llm_failure(self, game: GameRecord, round_id: str) -> None:
        """Caller holds the game lock."""
        log.error("game_ended_llm_failure", game_id=game.id, round_id=round_id)
        await self.repo.update_round(round_id, status=RoundStatus.VOIDED, ended_at=self.clock.now())
        await self._post(game.channel_id, *blocks.llm_failure_notice())
        self.scheduler.cancel_all(game.id)
        await self.repo.update_game(
            game.id, state=GameState.ENDED, ended_at=self.clock.now(), end_reason="llm_failure"
        )

    # ===================================================================================
    # Guessing (spec §3.6)
    # ===================================================================================

    async def submit_guess(self, user_id: str, round_id: str, text: str) -> None:
        """One final guess per guesser (spec §3.6)."""
        guess = text.strip()
        rnd = await self.repo.get_round(round_id)
        if rnd is None:
            raise UserFacingError("That round is over.")
        async with self._lock(rnd.game_id):
            live = await self._live_round(rnd.game_id, round_id, GameState.GUESSING)
            if live is None:
                raise UserFacingError("Guessing for that round is over.")
            game, rnd = live
            guessers = {g.user_id: g for g in await self.repo.get_round_guessers(round_id)}
            if user_id not in guessers:
                if user_id == rnd.writer_user_id:
                    raise UserFacingError("You wrote this one. Sit back and enjoy! 😄")
                raise UserFacingError(
                    "You weren't dealt into this round. You'll be in the next one!"
                )
            if rnd.guess_deadline is not None and self.clock.now() >= rnd.guess_deadline:
                raise UserFacingError("⏰ Time's up for this round.")
            if not MIN_GUESS <= len(guess) <= MAX_GUESS:
                raise UserFacingError(
                    f"Your guess must be {MIN_GUESS}–{MAX_GUESS} characters long."
                )
            previous = next(
                (g for g in await self.repo.get_guesses(round_id) if g.user_id == user_id), None
            )
            if previous is not None:
                raise UserFacingError(f"You already guessed: {previous.text}")
            try:
                await self.repo.add_guess(round_id, user_id, guess, self.clock.now())
            except DuplicateGuessError as exc:
                raise UserFacingError("You already guessed in this round.") from exc

            dm = guessers[user_id]
            if dm.dm_message_ts is not None and rnd.level is not None and rnd.jargon is not None:
                await self._update(
                    MessageRef(dm.dm_channel_id, dm.dm_message_ts),
                    *blocks.guess_prompt_submitted(rnd.number, rnd.level, rnd.jargon, guess),
                )
            self._counter_updates.call(
                round_id, lambda: self._refresh_guess_counter(rnd.game_id, round_id)
            )
            await self._touch(game.id)
            log.info("guess_submitted", game_id=game.id, round_id=round_id, user_id=user_id)
            close_round = await self._everyone_has_guessed(game)
        if close_round is not None:
            await self._close_guessing(rnd.game_id, close_round)

    async def on_guess_deadline(self, game_id: str, round_id: str) -> None:
        await self._close_guessing(game_id, round_id)

    async def _everyone_has_guessed(self, game: GameRecord) -> str | None:
        """The current round's id if it is GUESSING and nobody is left to guess
        (guessers who left the game are not waited for). Caller holds the lock."""
        if game.state is not GameState.GUESSING:
            return None
        rnd = await self.repo.get_current_round(game.id)
        if rnd is None or rnd.status is not RoundStatus.GUESSING:
            return None
        guessed = {g.user_id for g in await self.repo.get_guesses(rnd.id)}
        statuses = {p.user_id: p.status for p in await self.repo.get_players(game.id)}
        pending = [
            g.user_id
            for g in await self.repo.get_round_guessers(rnd.id)
            if g.user_id not in guessed and statuses.get(g.user_id) is not PlayerStatus.LEFT
        ]
        return None if pending else rnd.id

    async def _refresh_guess_counter(self, game_id: str, round_id: str) -> None:
        async with self._lock(game_id):
            live = await self._live_round(game_id, round_id, GameState.GUESSING)
            if live is None:
                return  # closed meanwhile: _close_guessing owns the message now
            game, rnd = live
            if rnd.status_message_ts is None or rnd.guess_deadline is None:
                return
            guessed = len(await self.repo.get_guesses(round_id))
            total = len(await self.repo.get_round_guessers(round_id))
            await self._update(
                MessageRef(game.channel_id, rnd.status_message_ts),
                *blocks.jargon_out(
                    rnd.number, rnd.writer_user_id, guessed, total, rnd.guess_deadline
                ),
            )

    async def _close_guessing(self, game_id: str, round_id: str) -> None:
        """Idempotent: only the first caller (early end, deadline, leave) closes the round."""
        async with self._lock(game_id):
            live = await self._live_round(game_id, round_id, GameState.GUESSING)
            if live is None:
                return
            game, rnd = live
            self._counter_updates.cancel(round_id)
            self.scheduler.cancel(game_id, TimerKind.GUESS_DEADLINE)
            await self.repo.update_round(round_id, status=RoundStatus.JUDGING)
            await self._transition(game, GameEvent.GUESSING_CLOSED)
            if rnd.status_message_ts is not None:
                guessed = len(await self.repo.get_guesses(round_id))
                total = len(await self.repo.get_round_guessers(round_id))
                await self._update(
                    MessageRef(game.channel_id, rnd.status_message_ts),
                    *blocks.guessing_closed(rnd.number, rnd.writer_user_id, guessed, total),
                )
            log.info("guessing_closed", game_id=game_id, round_id=round_id)

    # ===================================================================================
    # Helpers
    # ===================================================================================

    async def _live_round(
        self, game_id: str, round_id: str, expected: GameState
    ) -> tuple[GameRecord, RoundRecord] | None:
        """The game and round if the game is in ``expected`` state with ``round_id`` as its
        current round in the matching status. Otherwise ``None`` (stale work, discard it)."""
        game = await self.repo.get_game(game_id)
        rnd = await self.repo.get_current_round(game_id)
        if game is None or rnd is None or rnd.id != round_id or game.state is not expected:
            return None
        if rnd.status is not _ROUND_STATUS_FOR[expected]:
            return None
        return game, rnd

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


_ROUND_STATUS_FOR = {
    GameState.AWAITING_SENTENCE: RoundStatus.AWAITING_SENTENCE,
    GameState.GENERATING: RoundStatus.GENERATING,
    GameState.GUESSING: RoundStatus.GUESSING,
    GameState.JUDGING: RoundStatus.JUDGING,
}


class _AlreadySubmitted(UserFacingError):
    """The writer's sentence for this round was already accepted."""


def _in_other_game(game: GameRecord) -> str:
    return (
        f"You're already in a game in <#{game.channel_id}>. "
        "Leave it first with `/jargonator leave` there."
    )
