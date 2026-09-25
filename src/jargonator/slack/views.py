"""Slack modal views and their submission parsers (spec.md §6.2)."""

from dataclasses import dataclass
from typing import Any

from jargonator.config import Settings
from jargonator.domain.state import JargonLevel
from jargonator.slack import ids
from jargonator.slack.blocks import LEVEL_BADGES, escape
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


SENTENCE_BLOCK, GUESS_BLOCK = "sentence", "guess"


def _text_input(block_id: str, label: str, placeholder: str, max_length: int) -> Block:
    return {
        "type": "input",
        "block_id": block_id,
        "label": {"type": "plain_text", "text": label},
        "element": {
            "type": "plain_text_input",
            "action_id": "value",
            "max_length": max_length,
            "placeholder": {"type": "plain_text", "text": placeholder},
        },
    }


def sentence_modal(round_id: str) -> Block:
    return {
        "type": "modal",
        "callback_id": ids.SENTENCE_MODAL,
        "private_metadata": round_id,
        "title": {"type": "plain_text", "text": "Your sentence"},
        "submit": {"type": "plain_text", "text": "Jargonize it"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "Write one simple, true sentence about yourself. Keep it friendly!",
                },
            },
            _text_input(SENTENCE_BLOCK, "Sentence", "e.g. I ran a marathon last year", 150),
        ],
    }


def guess_modal(round_id: str, round_no: int, level: JargonLevel, jargon: str) -> Block:
    return {
        "type": "modal",
        "callback_id": ids.GUESS_MODAL,
        "private_metadata": round_id,
        "title": {"type": "plain_text", "text": f"Round {round_no} guess"},
        "submit": {"type": "plain_text", "text": "Submit guess"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": f"*Level:* {LEVEL_BADGES[level]}"}],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"> {escape(jargon)}"}},
            _text_input(GUESS_BLOCK, "What did they actually say?", "One guess, no edits", 200),
        ],
    }


@dataclass(frozen=True)
class StartParams:
    channel_id: str
    guess_seconds: int
    writer_seconds: int
    join_window_seconds: int


def _value(view: dict[str, Any], block_id: str) -> str | None:
    value = view.get("state", {}).get("values", {}).get(block_id, {}).get("value", {}).get("value")
    return None if value is None else str(value)


def parse_start_submission(view: dict[str, Any]) -> StartParams | dict[str, str]:
    """Validated settings, or ``{block_id: error}`` for Slack's inline errors."""
    values: dict[str, int] = {}
    errors: dict[str, str] = {}
    for block_id, _label, low, high in START_FIELDS:
        raw = _value(view, block_id)
        try:
            number = int(raw) if raw is not None else None
        except ValueError:
            number = None
        if number is None or not low <= number <= high:
            errors[block_id] = f"Enter a whole number from {low} to {high}."
        else:
            values[block_id] = number
    if errors:
        return errors
    return StartParams(
        channel_id=str(view.get("private_metadata", "")),
        guess_seconds=values["guess_seconds"],
        writer_seconds=values["writer_seconds"],
        join_window_seconds=values["join_window_seconds"],
    )


def parse_text_submission(
    view: dict[str, Any], block_id: str, low: int, high: int
) -> str | dict[str, str]:
    text = (_value(view, block_id) or "").strip()
    if not low <= len(text) <= high:
        return {block_id: f"Please write {low}–{high} characters."}
    return text
