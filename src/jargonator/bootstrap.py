"""Composition root: builds and owns the long-lived components (spec.md §12).

Each infrastructure step adds its component here, so nothing is left unwired.
"""

import asyncio
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from jargonator.config import Settings
from jargonator.db.repo import Repo
from jargonator.db.session import create_engine_and_sessionmaker, run_migrations


@dataclass
class Container:
    settings: Settings
    db_engine: AsyncEngine
    repo: Repo

    async def aclose(self) -> None:
        await self.db_engine.dispose()


async def build_container(settings: Settings) -> Container:
    """Run migrations, then build every component."""
    await asyncio.to_thread(run_migrations, settings.database_url)
    engine, sessionmaker = create_engine_and_sessionmaker(settings.database_url)
    return Container(settings=settings, db_engine=engine, repo=Repo(sessionmaker))
