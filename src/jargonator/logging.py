"""Structured JSON logging (spec.md §14).

Never log user sentences or guesses above DEBUG, and never log tokens.
"""

import logging
import sys

import structlog

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}


def configure_logging(level: str) -> None:
    """Configure structlog (and stdlib logging) to emit JSON lines on stdout."""
    level_name = level.upper()
    if level_name not in _LEVELS:
        raise ValueError(f"Unknown log level: {level!r}")
    numeric_level = logging.getLevelNamesMapping()[level_name]

    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=numeric_level, force=True)

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
