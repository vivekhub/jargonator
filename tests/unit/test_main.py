import json

import pytest
import structlog

from jargonator import __version__
from jargonator.main import main


def test_main_logs_startup(
    required_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)  # type: ignore[arg-type]  # ensure no stray .env is read
    main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    startup = [line for line in lines if line["event"] == "startup"]
    assert startup and startup[0]["version"] == __version__
    # Secrets must never be logged.
    assert all("xoxb-test" not in json.dumps(line) for line in lines)
    structlog.reset_defaults()
