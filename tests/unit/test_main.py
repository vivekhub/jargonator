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
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/main.db")
    from jargonator import main as main_module

    async def fake_run(settings: object) -> None:
        import structlog

        structlog.get_logger().info("container_ready")

    monkeypatch.setattr(main_module, "run", fake_run)
    main()
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    startup = [line for line in lines if line["event"] == "startup"]
    assert startup and startup[0]["version"] == __version__
    assert any(line["event"] == "container_ready" for line in lines)
    # Secrets must never be logged.
    assert all("xoxb-test" not in json.dumps(line) for line in lines)
    structlog.reset_defaults()
