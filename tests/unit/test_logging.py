import json
import logging

import pytest
import structlog

from jargonator.logging import configure_logging


@pytest.fixture(autouse=True)
def _reset_structlog() -> None:
    yield  # type: ignore[misc]
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()


def _last_json_line(out: str) -> dict[str, object]:
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return json.loads(lines[-1])  # type: ignore[no-any-return]


def test_emits_json_with_event_and_level(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger().info("hello", foo="bar")
    record = _last_json_line(capsys.readouterr().out)
    assert record["event"] == "hello"
    assert record["level"] == "info"
    assert record["foo"] == "bar"
    assert "timestamp" in record


def test_merges_contextvars(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.contextvars.bind_contextvars(game_id="g1", round_id="r1")
    structlog.get_logger().info("with_context")
    record = _last_json_line(capsys.readouterr().out)
    assert record["game_id"] == "g1"
    assert record["round_id"] == "r1"


def test_level_filters_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("INFO")
    structlog.get_logger().debug("hidden")
    assert "hidden" not in capsys.readouterr().out


def test_debug_level_shows_debug(capsys: pytest.CaptureFixture[str]) -> None:
    configure_logging("DEBUG")
    structlog.get_logger().debug("visible")
    assert _last_json_line(capsys.readouterr().out)["event"] == "visible"


def test_invalid_level_rejected() -> None:
    with pytest.raises(ValueError):
        configure_logging("LOUD")


def test_stdlib_logging_level_set() -> None:
    configure_logging("WARNING")
    assert logging.getLogger().level == logging.WARNING
