import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from jargonator.bootstrap import Container
from jargonator.config import Settings
from jargonator.main import RuntimeFactories, run


class FakeSocketHandler:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.connected = False

    async def connect_async(self) -> None:
        self.events.append("connect")
        self.connected = True

    async def close_async(self) -> None:
        self.events.append("close")
        self.connected = False

    async def is_connected(self) -> bool:
        return self.connected


async def test_run_starts_recovers_and_shuts_down_cleanly(
    required_env: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path / 'run.db'}")
    import socket

    with socket.socket() as probe:  # find a free port for the health server
        probe.bind(("127.0.0.1", 0))
        free_port = probe.getsockname()[1]
    monkeypatch.setenv("HEALTH_PORT", str(free_port))
    from jargonator.logging import configure_logging

    configure_logging("INFO")
    handler = FakeSocketHandler()
    stop = asyncio.Event()
    containers: list[Container] = []
    recovered: list[bool] = []

    def make_handler(app: Any, app_token: str) -> FakeSocketHandler:
        assert app_token == "xapp-test"
        return handler

    async def on_container(container: Container) -> None:
        containers.append(container)
        original = container.engine.recover

        async def recover() -> None:
            recovered.append(True)
            await original()

        container.engine.recover = recover  # type: ignore[method-assign]

    factories = RuntimeFactories(
        socket_handler=make_handler,
        stop_event=stop,
        on_container=on_container,
        install_signal_handlers=False,
    )
    task = asyncio.create_task(run(Settings(_env_file=None), factories))  # type: ignore[call-arg]
    async with asyncio.timeout(5):
        while not handler.connected:  # noqa: ASYNC110 (test helper: polling is the point)
            await asyncio.sleep(0.01)
    assert recovered == [True]
    import aiohttp

    url = f"http://127.0.0.1:{free_port}/healthz"
    async with aiohttp.ClientSession() as session, session.get(url) as response:
        assert response.status == 200
        assert (await response.json())["socket"] == "connected"
    stop.set()
    await asyncio.wait_for(task, 5)

    assert handler.events == ["connect", "close"]
    assert containers[0].timers.pending() == {}
    events = [
        json.loads(line)["event"]
        for line in capsys.readouterr().out.splitlines()
        if line.startswith("{")
    ]
    for expected in (
        "container_ready",
        "health_server_started",
        "socket_connected",
        "shutdown_complete",
    ):
        assert expected in events
    assert events.index("socket_connected") < events.index("shutdown_complete")
