from typing import Any

import pytest

from jargonator.domain.state import JargonLevel
from jargonator.slack import ids
from jargonator.slack.views import (
    StartParams,
    guess_modal,
    parse_start_submission,
    parse_text_submission,
    sentence_modal,
)
from tests.snapshot import assert_snapshot


def start_view(guess: str | None, writer: str | None, join: str | None) -> dict[str, Any]:
    def value(v: str | None) -> dict[str, Any]:
        return {"value": {"type": "number_input", "value": v}}

    return {
        "private_metadata": "C1",
        "state": {
            "values": {
                "guess_seconds": value(guess),
                "writer_seconds": value(writer),
                "join_window_seconds": value(join),
            }
        },
    }


def text_view(text: str | None, block_id: str) -> dict[str, Any]:
    return {
        "private_metadata": "round-9",
        "state": {"values": {block_id: {"value": {"type": "plain_text_input", "value": text}}}},
    }


def test_sentence_modal_snapshot() -> None:
    view = sentence_modal("round-9")
    assert_snapshot("sentence_modal", view)
    assert view["callback_id"] == ids.SENTENCE_MODAL and view["private_metadata"] == "round-9"


def test_guess_modal_snapshot() -> None:
    view = guess_modal("round-9", 2, JargonLevel.SPICY, "<b>synergy</b>")
    assert_snapshot("guess_modal", view)
    assert view["callback_id"] == ids.GUESS_MODAL
    assert "&lt;b&gt;synergy" in str(view)


def test_parse_start_submission_ok() -> None:
    assert parse_start_submission(start_view("45", "120", "0")) == StartParams("C1", 45, 120, 0)


@pytest.mark.parametrize(
    ("values", "bad_field"),
    [
        (("10", "90", "0"), "guess_seconds"),
        (("60", "900", "0"), "writer_seconds"),
        (("60", "90", "-1"), "join_window_seconds"),
        (("abc", "90", "0"), "guess_seconds"),
        ((None, "90", "0"), "guess_seconds"),
    ],
)
def test_parse_start_submission_errors(values: tuple[str | None, ...], bad_field: str) -> None:
    result = parse_start_submission(start_view(*values))
    assert isinstance(result, dict) and set(result) == {bad_field}


def test_parse_text_submission() -> None:
    assert (
        parse_text_submission(text_view("  I have cats ", "sentence"), "sentence", 5, 150)
        == "I have cats"
    )
    assert parse_text_submission(text_view("hi", "sentence"), "sentence", 5, 150) == {
        "sentence": "Please write 5–150 characters."
    }
    assert isinstance(parse_text_submission(text_view(None, "guess"), "guess", 1, 200), dict)
    assert isinstance(parse_text_submission(text_view("x" * 201, "guess"), "guess", 1, 200), dict)
