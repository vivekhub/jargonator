from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jargonator.db.repo import Repo
from jargonator.db.session import create_engine_and_sessionmaker, run_migrations


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    run_migrations(url)
    return url


@pytest.fixture
async def repo(db_url: str) -> AsyncIterator[Repo]:
    engine, sessionmaker = create_engine_and_sessionmaker(db_url)
    yield Repo(sessionmaker)
    await engine.dispose()
