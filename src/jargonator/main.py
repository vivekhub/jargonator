"""Process entry point (spec.md §12, §14). Grows in later steps."""

import asyncio

import structlog

from jargonator import __version__
from jargonator.bootstrap import build_container
from jargonator.config import Settings
from jargonator.logging import configure_logging
from jargonator.slack.gateway import BoltSlackGateway, build_web_client


async def run(settings: Settings) -> None:
    log = structlog.get_logger()
    web_client = build_web_client(settings.slack_bot_token.get_secret_value())
    container = await build_container(settings, slack=BoltSlackGateway(web_client))
    try:
        log.info("container_ready")
    finally:
        await container.aclose()


def main() -> None:
    """Load configuration, set up logging and build the application."""
    settings = Settings()  # values come from the environment / .env
    configure_logging(settings.log_level)
    structlog.get_logger().info("startup", version=__version__)
    asyncio.run(run(settings))
