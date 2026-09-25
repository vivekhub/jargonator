from pathlib import Path

import pytest

from jargonator.bootstrap import build_container
from jargonator.config import Settings


async def test_build_container_against_tmp_db(
    required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'c.db'}")
    container = await build_container(Settings(_env_file=None))  # type: ignore[call-arg]
    try:
        assert await container.repo.get_active_game_by_channel("C1") is None
        assert container.settings.min_players == 2
        assert container.llm_client.fallback_model == "openai/gpt-4o-mini"
    finally:
        await container.aclose()
