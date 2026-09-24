"""Process entry point and composition root (spec.md §12, §14). Grows in later steps."""

import structlog

from jargonator import __version__
from jargonator.config import Settings
from jargonator.logging import configure_logging


def main() -> None:
    """Load configuration, set up logging and announce startup."""
    settings = Settings()  # values come from the environment / .env
    configure_logging(settings.log_level)
    structlog.get_logger().info("startup", version=__version__)
