"""Structured JSON logging (spec.md §14).

Both structlog and stdlib loggers (alembic, slack_bolt, aiohttp, ...) emit JSON lines on
stdout. Never log user sentences or guesses above DEBUG, and never log tokens.
"""

import logging
import sys

import structlog
from structlog.typing import Processor

_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_NOISY_LOGGERS = {"alembic": logging.WARNING, "sqlalchemy.engine": logging.WARNING}


def configure_logging(level: str) -> None:
    """Configure structlog and stdlib logging to emit JSON lines on stdout."""
    level_name = level.upper()
    if level_name not in _LEVELS:
        raise ValueError(f"Unknown log level: {level!r}")
    numeric_level = logging.getLevelNamesMapping()[level_name]

    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=[*shared, structlog.stdlib.add_logger_name],
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(numeric_level)
    for name, minimum in _NOISY_LOGGERS.items():
        logging.getLogger(name).setLevel(max(minimum, numeric_level))

    structlog.configure(
        processors=[
            *shared,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=False,
    )
