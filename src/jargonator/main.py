"""Process entry point (spec.md §12, §14).

Startup: migrations → container → health server → recover live games → Bolt app →
Socket Mode. Shutdown (SIGTERM/SIGINT): close the socket, stop timers and background
work, close the LLM client and the DB, stop the health server.
"""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog
from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
from slack_bolt.async_app import AsyncApp
from sqlalchemy import text

from jargonator import __version__
from jargonator.bootstrap import Container, build_container
from jargonator.config import Settings
from jargonator.health import HealthState, build_health_app, start_health_server
from jargonator.logging import configure_logging
from jargonator.slack.app import build_bolt_app
from jargonator.slack.gateway import BoltSlackGateway, build_web_client

log = structlog.get_logger()


class SocketHandler(Protocol):
    async def connect_async(self) -> None: ...

    async def close_async(self) -> None: ...


def _socket_mode_handler(app: AsyncApp, app_token: str) -> SocketHandler:
    return AsyncSocketModeHandler(app, app_token)


async def _no_op(_: Container) -> None:
    return None


@dataclass
class RuntimeFactories:
    """Seams so ``run`` can be tested without Slack or signals."""

    socket_handler: Callable[[AsyncApp, str], Any] = _socket_mode_handler
    stop_event: asyncio.Event = field(default_factory=asyncio.Event)
    on_container: Callable[[Container], Awaitable[None]] = _no_op
    install_signal_handlers: bool = True


def _socket_check(handler: Any) -> Callable[[], Awaitable[bool]]:
    async def check() -> bool:
        if hasattr(handler, "is_connected"):
            return bool(await handler.is_connected())
        client = getattr(handler, "client", None)
        return bool(client is not None and await client.is_connected())

    return check


async def run(settings: Settings, factories: RuntimeFactories | None = None) -> None:
    factories = factories or RuntimeFactories()
    web_client = build_web_client(settings.slack_bot_token.get_secret_value())
    container = await build_container(settings, slack=BoltSlackGateway(web_client))
    log.info("container_ready")
    await factories.on_container(container)

    async def db_check() -> bool:
        async with container.db_engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True

    health = HealthState(db_check=db_check)
    runner = await start_health_server(build_health_app(health), settings.health_port)
    log.info("health_server_started", port=settings.health_port)
    handler: Any = None
    stop = factories.stop_event
    try:
        if factories.install_signal_handlers:
            loop = asyncio.get_running_loop()
            for sig in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(sig, stop.set)
        await container.engine.recover()
        app = build_bolt_app(settings, container.engine, web_client)
        handler = factories.socket_handler(app, settings.slack_app_token.get_secret_value())
        # Race the connection against a stop request: with a bad app token, Socket Mode
        # retries forever, and SIGTERM must still shut down cleanly.
        connecting = asyncio.create_task(handler.connect_async())
        stopping = asyncio.create_task(stop.wait())
        await asyncio.wait({connecting, stopping}, return_when=asyncio.FIRST_COMPLETED)
        if connecting.done():
            connecting.result()  # re-raise a connection error
            health.socket_check = _socket_check(handler)
            log.info("socket_connected")
            await stopping
        else:
            connecting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await connecting
        log.info("shutdown_requested")
    finally:
        if handler is not None:
            await handler.close_async()
        await container.aclose()
        await runner.cleanup()
        log.info("shutdown_complete")


def main() -> None:
    """Load configuration, set up logging and run the bot until stopped."""
    settings = Settings()  # values come from the environment / .env
    configure_logging(settings.log_level)
    structlog.get_logger().info("startup", version=__version__)
    asyncio.run(run(settings))
