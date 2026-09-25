import logging
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import structlog

if TYPE_CHECKING:
    from tests.engine_harness import Harness

REQUIRED_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "OPENROUTER_API_KEY": "sk-or-test",
}


@pytest.fixture(autouse=True)
def _isolate_logging() -> Iterator[None]:
    """Undo configure_logging after each test (review finding 7).

    Otherwise a root handler bound to one test's captured stdout outlives it and later
    library log records hit a closed stream ("--- Logging error ---").
    """
    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    root.handlers[:] = handlers
    root.setLevel(level)
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set only the required env vars and clear any optional overrides."""
    from jargonator.config import Settings

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return dict(REQUIRED_ENV)


@pytest.fixture
async def harness(tmp_path: "Path", required_env: dict[str, str]) -> "AsyncIterator[Harness]":
    from tests.engine_harness import make_harness

    h = await make_harness(tmp_path)
    yield h
    await h.aclose()
