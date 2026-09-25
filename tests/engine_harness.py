"""Shared setup for engine tests: a real SQLite repo plus fakes for everything else."""

import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine

from jargonator.config import Settings
from jargonator.db.records import GameRecord, PlayerRecord, RoundRecord
from jargonator.db.repo import Repo
from jargonator.db.session import create_engine_and_sessionmaker, run_migrations
from jargonator.engine.game_engine import GameEngine
from jargonator.engine.scheduler import Scheduler
from jargonator.slack.gateway import MessageRef
from tests.fakes.clock import FakeClock
from tests.fakes.llm import FakeLLM
from tests.fakes.scheduler import RecordingScheduler
from tests.fakes.slack import FakeMessage, FakeSlackGateway

T0 = datetime(2026, 3, 2, 10, 0, tzinfo=UTC)
CHANNEL = "C1"


def make_settings(**overrides: Any) -> Settings:
    return Settings(  # type: ignore[call-arg]
        _env_file=None,
        slack_bot_token="xoxb-test",
        slack_app_token="xapp-test",
        openrouter_api_key="sk-or-test",
        **overrides,
    )


@dataclass
class Harness:
    engine: GameEngine
    repo: Repo
    slack: FakeSlackGateway
    llm: FakeLLM
    clock: FakeClock
    scheduler: Scheduler
    settings: Settings
    db_engine: AsyncEngine
    channel: str = CHANNEL
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def recorder(self) -> RecordingScheduler:
        assert isinstance(self.scheduler, RecordingScheduler)
        return self.scheduler

    # --- lookups ------------------------------------------------------------------------

    async def game(self) -> GameRecord:
        game = await self.repo.get_active_game_by_channel(self.channel)
        if game is None:  # fall back to the most recent (possibly ended) game
            games = self.extra.get("game_ids", [])
            assert games, "no game created"
            found = await self.repo.get_game(games[-1])
            assert found is not None
            return found
        return game

    async def players(self) -> list[PlayerRecord]:
        return await self.repo.get_players((await self.game()).id)

    async def player(self, user: str) -> PlayerRecord:
        found = await self.repo.get_player((await self.game()).id, user)
        assert found is not None
        return found

    async def round(self) -> RoundRecord:
        rnd = await self.repo.get_current_round((await self.game()).id)
        assert rnd is not None
        return rnd

    async def lobby_message(self) -> FakeMessage:
        game = await self.game()
        assert game.lobby_message_ts is not None
        return self.slack.current(MessageRef(self.channel, game.lobby_message_ts))

    def channel_texts(self) -> list[str]:
        return [m.text for m in self.slack.messages_in(self.channel)]

    # --- common actions ---------------------------------------------------------------------

    async def create(
        self, host: str = "U1", *, join_window: int = 0, guess: int = 60, writer: int = 90
    ) -> GameRecord:
        game = await self.engine.create_game(
            channel_id=self.channel,
            user_id=host,
            guess_seconds=guess,
            writer_seconds=writer,
            join_window_seconds=join_window,
        )
        self.extra.setdefault("game_ids", []).append(game.id)
        return game

    async def lobby_with(self, *users: str) -> GameRecord:
        """Create a game hosted by the first user, and join the rest."""
        game = await self.create(users[0])
        for user in users[1:]:
            await self.engine.join(self.channel, user)
        return game

    async def started(self, *users: str) -> RoundRecord:
        """Lobby with ``users`` (first is host), started: round 1 awaiting the writer."""
        await self.lobby_with(*users)
        await self.engine.start_game(self.channel, users[0])
        return await self.round()

    async def write(self, sentence: str = "I have two cats") -> RoundRecord:
        """The current writer submits ``sentence`` (and generation runs)."""
        rnd = await self.round()
        await self.engine.submit_sentence(rnd.writer_user_id, rnd.id, sentence)
        return await self.round()

    async def guessers(self) -> list[str]:
        rnd = await self.round()
        return [g.user_id for g in await self.repo.get_round_guessers(rnd.id)]

    async def aclose(self) -> None:
        await self.db_engine.dispose()


async def make_harness(
    tmp_path: Path,
    *,
    scheduler: Scheduler | None = None,
    clock: FakeClock | None = None,
    llm: FakeLLM | None = None,
    slack: FakeSlackGateway | None = None,
    db_name: str = "engine.db",
    **settings_overrides: Any,
) -> Harness:
    url = f"sqlite+aiosqlite:///{tmp_path / db_name}"
    run_migrations(url)
    db_engine, sessionmaker = create_engine_and_sessionmaker(url)
    repo = Repo(sessionmaker)
    settings = make_settings(**settings_overrides)
    clock = clock or FakeClock(T0)
    slack = slack or FakeSlackGateway()
    llm = llm or FakeLLM()
    scheduler = scheduler or RecordingScheduler()
    engine = GameEngine(
        repo=repo,
        slack=slack,
        llm=llm,
        clock=clock,
        scheduler=scheduler,
        settings=settings,
        rng=random.Random(7),
    )
    return Harness(engine, repo, slack, llm, clock, scheduler, settings, db_engine)
