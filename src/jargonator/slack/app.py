"""Builds the Bolt app and registers every listener (spec.md §4, §6)."""

from collections.abc import Awaitable, Callable
from typing import Any

from slack_bolt.async_app import AsyncApp
from slack_sdk.web.async_client import AsyncWebClient

from jargonator.config import Settings
from jargonator.engine.game_engine import GameEngine
from jargonator.slack.commands import handle_jargonator


def build_bolt_app(
    settings: Settings,
    engine: GameEngine,
    client: AsyncWebClient,
    *,
    authorize: Callable[..., Awaitable[Any]] | None = None,
) -> AsyncApp:
    """``authorize`` is only for tests (avoids the auth.test network call)."""
    app = AsyncApp(
        client=client,
        request_verification_enabled=False,  # Socket Mode: no HTTP signature to verify
        authorize=authorize,
    )

    @app.command("/jargonator")
    async def _jargonator(ack: Any, command: dict[str, Any], respond: Any, client: Any) -> None:
        await handle_jargonator(
            ack=ack,
            command=command,
            respond=respond,
            client=client,
            engine=engine,
            settings=settings,
        )

    return app
