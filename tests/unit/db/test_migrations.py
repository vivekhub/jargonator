from pathlib import Path

import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext

from jargonator.db.models import Base
from jargonator.db.session import run_migrations, sync_url

TABLES = {"games", "players", "turn_order", "rounds", "round_guessers", "guesses"}


def test_migration_creates_every_table(db_url: str) -> None:
    engine = sa.create_engine(sync_url(db_url))
    with engine.connect() as conn:
        assert set(sa.inspect(conn).get_table_names()) >= TABLES
    engine.dispose()


def test_models_match_migrated_schema(db_url: str) -> None:
    engine = sa.create_engine(sync_url(db_url))
    with engine.connect() as conn:
        diffs = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diffs == []


def test_migrations_are_idempotent(db_url: str) -> None:
    run_migrations(db_url)  # already at head: must be a no-op


def test_run_migrations_creates_missing_parent_directory(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "dir" / "game.db"
    run_migrations(f"sqlite+aiosqlite:///{target}")
    assert target.exists()


def test_sync_url() -> None:
    assert sync_url("sqlite+aiosqlite:////data/x.db") == "sqlite:////data/x.db"
