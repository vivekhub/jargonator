"""Composition root: builds and owns the long-lived components (spec.md §12).

Each infrastructure step adds its component here, so nothing is left unwired.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from jargonator.config import Settings
from jargonator.db.repo import Repo
from jargonator.db.session import create_engine_and_sessionmaker, run_migrations
from jargonator.llm.client import OpenRouterClient, build_openrouter_client
from jargonator.llm.tasks import LLMTasks, OpenRouterTasks


@dataclass
class Container:
    settings: Settings
    db_engine: AsyncEngine
    repo: Repo
    llm_client: OpenRouterClient
    llm: LLMTasks

    async def aclose(self) -> None:
        await self.llm_client.aclose()
        await self.db_engine.dispose()


async def build_container(settings: Settings) -> Container:
    """Run migrations, then build every component."""
    await asyncio.to_thread(run_migrations, settings.database_url)
    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    llm_client = build_openrouter_client(settings)
    return Container(
        settings=settings,
        db_engine=engine,
        repo=Repo(sessionmaker),
        llm_client=llm_client,
        llm=OpenRouterTasks(llm_client),
    )
