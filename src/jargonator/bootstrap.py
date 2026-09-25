"""Composition root: builds and owns the long-lived components (spec.md §12).

Each infrastructure step adds its component here, so nothing is left unwired.
"""

import asyncio
import random
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from jargonator.clock import Clock, SystemClock
from jargonator.config import Settings
from jargonator.db.repo import Repo
from jargonator.db.session import create_engine_and_sessionmaker, run_migrations
from jargonator.engine.game_engine import GameEngine
from jargonator.engine.timers import TimerService
from jargonator.llm.client import OpenRouterClient, build_openrouter_client
from jargonator.llm.tasks import LLMTasks, OpenRouterTasks
from jargonator.slack.gateway import SlackGateway


@dataclass
class Container:
    settings: Settings
    db_engine: AsyncEngine
    repo: Repo
    llm_client: OpenRouterClient
    llm: LLMTasks
    slack: SlackGateway
    clock: Clock
    timers: TimerService
    engine: GameEngine

    async def aclose(self) -> None:
        """Shut down in dependency order: timers first, so nothing fires mid-teardown."""
        await self.timers.shutdown()
        await self.engine.aclose()
        await self.llm_client.aclose()
        await self.db_engine.dispose()


async def build_container(
    settings: Settings, *, slack: SlackGateway, clock: Clock | None = None
) -> Container:
    """Run migrations, then build every component."""
    await asyncio.to_thread(run_migrations, settings.database_url)
    db_engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    repo = Repo(sessionmaker)
    llm_client = build_openrouter_client(settings)
    llm = OpenRouterTasks(llm_client)
    clock = clock or SystemClock()
    timers = TimerService(clock)
    engine = GameEngine(
        repo=repo,
        slack=slack,
        llm=llm,
        clock=clock,
        scheduler=timers,
        settings=settings,
        rng=random.Random(),
    )
    timers.bind(engine.dispatch_timer)
    return Container(
        settings=settings,
        db_engine=db_engine,
        repo=repo,
        llm_client=llm_client,
        llm=llm,
        slack=slack,
        clock=clock,
        timers=timers,
        engine=engine,
    )
