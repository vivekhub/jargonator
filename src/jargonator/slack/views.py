"""Slack modal views (spec.md §6.2)."""

from jargonator.config import Settings
from jargonator.slack import ids
from jargonator.slack.gateway import Block

START_FIELDS = (
    # (block_id, label, min, max)
    ("guess_seconds", "Guess time (seconds)", 20, 300),
    ("writer_seconds", "Writer time (seconds)", 30, 600),
    ("join_window_seconds", "Auto-start after (seconds, 0 = host starts)", 0, 600),
)


def _number_input(block_id: str, label: str, initial: int, low: int, high: int) -> Block:
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": {
            "type": "number_input",
            "action_id": "value",
            "is_decimal_allowed": False,
            "initial_value": str(initial),
            "min_value": str(low),
            "max_value": str(high),
        },
    }


def start_modal(settings: Settings, channel_id: str) -> Block:
    defaults = {
        "guess_seconds": settings.default_guess_seconds,
        "writer_seconds": settings.default_writer_seconds,
        "join_window_seconds": settings.default_join_window_seconds,
    }
    return {
        "type": "modal",
        "callback_id": ids.START_MODAL,
        "private_metadata": channel_id,
        "title": {"type": "plain_text", "text": "Start Jargonator"},
        "submit": {"type": "plain_text", "text": "Open lobby"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            _number_input(block_id, label, defaults[block_id], low, high)
            for block_id, label, low, high in START_FIELDS
        ],
    }
