"""Engine/session construction and programmatic migrations (spec.md §10)."""

from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
SQLITE_BUSY_TIMEOUT_SECONDS = 30


def sync_url(url: str) -> str:
    """Strip the async driver (``sqlite+aiosqlite`` → ``sqlite``) for alembic."""
    parsed = make_url(url)
    return parsed.set(drivername=parsed.get_backend_name()).render_as_string(hide_password=False)


def _ensure_sqlite_parent_dir(url: str) -> None:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)


def run_migrations(url: str) -> None:
    """Upgrade the database to the latest revision. Synchronous: call it via a thread."""
    _ensure_sqlite_parent_dir(url)
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    config.set_main_option("sqlalchemy.url", sync_url(url).replace("%", "%%"))
    command.upgrade(config, "head")


def _set_sqlite_pragmas(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()


def create_engine_and_sessionmaker(
    url: str,
) -> tuple[AsyncEngine, async_sessionmaker[Any]]:
    """Create the async engine (with SQLite pragmas) and a session factory."""
    _ensure_sqlite_parent_dir(url)
    is_sqlite = make_url(url).get_backend_name() == "sqlite"
    engine = create_async_engine(
        url, connect_args={"timeout": SQLITE_BUSY_TIMEOUT_SECONDS} if is_sqlite else {}
    )
    if is_sqlite:
        event.listen(engine.sync_engine, "connect", _set_sqlite_pragmas)
    return engine, async_sessionmaker(engine, expire_on_commit=False)
