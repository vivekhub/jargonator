from typing import Any
from unittest.mock import AsyncMock

import pytest

from jargonator.domain.state import JargonLevel
from jargonator.engine.errors import UserFacingError
from jargonator.slack import actions, ids
from tests.engine_harness import make_settings


def button_body(action_id: str, value: str, channel: str = "C1") -> dict[str, Any]:
    return {
        "type": "block_actions",
        "user": {"id": "U1"},
        "channel": {"id": channel},
        "trigger_id": "trig",
        "actions": [{"action_id": action_id, "value": value}],
    }


def view_body(user: str = "U1") -> dict[str, Any]:
    return {"type": "view_submission", "user": {"id": user}}


@pytest.fixture
def deps() -> dict[str, Any]:
    return {
        "ack": AsyncMock(),
        "respond": AsyncMock(),
        "client": AsyncMock(),
        "engine": AsyncMock(),
    }


@pytest.mark.parametrize(
    ("action_id", "value", "method", "args"),
    [
        (ids.JOIN, "g1", "join", ("C1", "U1")),
        (ids.START, "g1", "start_game", ("C1", "U1")),
        (ids.NEXT, "g1", "next_round", ("C1", "U1")),
        (ids.END, "g1", "end_game", ("C1", "U1")),
        (ids.CLAIM_HOST, "r1", "claim_host", ("C1", "U1", "r1")),
    ],
)
async def test_channel_buttons_call_engine(
    deps: dict[str, Any], action_id: str, value: str, method: str, args: tuple[str, ...]
) -> None:
    await actions.handle_button(body=button_body(action_id, value), **deps)
    deps["ack"].assert_awaited_once()
    getattr(deps["engine"], method).assert_awaited_once_with(*args)


async def test_button_error_is_ephemeral(deps: dict[str, Any]) -> None:
    deps["engine"].start_game = AsyncMock(side_effect=UserFacingError("Only the host can do that."))
    await actions.handle_button(body=button_body(ids.START, "g1"), **deps)
    kwargs = deps["respond"].await_args.kwargs
    assert kwargs["text"] == "Only the host can do that."
    assert kwargs["response_type"] == "ephemeral" and kwargs["replace_original"] is False


async def test_write_button_opens_sentence_modal(deps: dict[str, Any]) -> None:
    await actions.handle_button(body=button_body(ids.WRITE_SENTENCE, "r1", "DU1"), **deps)
    deps["engine"].check_can_write.assert_awaited_once_with("U1", "r1")
    view = deps["client"].views_open.await_args.kwargs["view"]
    assert view["callback_id"] == ids.SENTENCE_MODAL and view["private_metadata"] == "r1"


async def test_try_again_opens_sentence_modal(deps: dict[str, Any]) -> None:
    await actions.handle_button(body=button_body(ids.TRY_AGAIN, "r1", "DU1"), **deps)
    assert deps["client"].views_open.await_args.kwargs["view"]["callback_id"] == ids.SENTENCE_MODAL


async def test_stale_write_button_explains_instead_of_opening(deps: dict[str, Any]) -> None:
    deps["engine"].check_can_write = AsyncMock(side_effect=UserFacingError("That round is over."))
    await actions.handle_button(body=button_body(ids.WRITE_SENTENCE, "r1", "DU1"), **deps)
    deps["client"].views_open.assert_not_awaited()
    assert deps["respond"].await_args.kwargs["text"] == "That round is over."


async def test_guess_button_opens_guess_modal(deps: dict[str, Any]) -> None:
    deps["engine"].guess_context = AsyncMock(return_value=(2, JargonLevel.MILD, "synergy"))
    await actions.handle_button(body=button_body(ids.SUBMIT_GUESS, "r1", "DU1"), **deps)
    view = deps["client"].views_open.await_args.kwargs["view"]
    assert view["callback_id"] == ids.GUESS_MODAL and "synergy" in str(view)


async def test_start_modal_submission_creates_game(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import start_view

    await actions.handle_start_modal(
        body=view_body(),
        view=start_view("60", "90", "120"),
        settings=make_settings(),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    deps["ack"].assert_awaited_once_with()
    deps["engine"].create_game.assert_awaited_once_with(
        channel_id="C1", user_id="U1", guess_seconds=60, writer_seconds=90, join_window_seconds=120
    )


async def test_start_modal_validation_errors_inline(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import start_view

    await actions.handle_start_modal(
        body=view_body(),
        view=start_view("5", "90", "120"),
        settings=make_settings(),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    kwargs = deps["ack"].await_args.kwargs
    assert kwargs["response_action"] == "errors" and "guess_seconds" in kwargs["errors"]
    deps["engine"].create_game.assert_not_awaited()


async def test_start_modal_failure_is_reported(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import start_view

    deps["engine"].create_game = AsyncMock(side_effect=UserFacingError("Invite me with /invite"))
    await actions.handle_start_modal(
        body=view_body(),
        view=start_view("60", "90", "0"),
        settings=make_settings(),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    deps["client"].chat_postEphemeral.assert_awaited_once()
    assert "/invite" in deps["client"].chat_postEphemeral.await_args.kwargs["text"]


async def test_sentence_modal_acks_then_submits(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import text_view

    order: list[str] = []
    deps["ack"].side_effect = lambda *a, **k: order.append("ack")
    deps["engine"].submit_sentence = AsyncMock(side_effect=lambda *a: order.append("submit"))
    await actions.handle_sentence_modal(
        body=view_body(),
        view=text_view("I have two cats", "sentence"),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    assert order == ["ack", "submit"]
    deps["engine"].submit_sentence.assert_awaited_once_with("U1", "round-9", "I have two cats")


async def test_sentence_modal_inline_length_error(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import text_view

    await actions.handle_sentence_modal(
        body=view_body(),
        view=text_view("hi", "sentence"),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    assert deps["ack"].await_args.kwargs["response_action"] == "errors"
    deps["engine"].submit_sentence.assert_not_awaited()


async def test_guess_modal_error_goes_to_dm(deps: dict[str, Any]) -> None:
    from tests.unit.slack.test_views import text_view

    deps["engine"].submit_guess = AsyncMock(side_effect=UserFacingError("⏰ Time's up"))
    await actions.handle_guess_modal(
        body=view_body(),
        view=text_view("I own kitties", "guess"),
        ack=deps["ack"],
        client=deps["client"],
        engine=deps["engine"],
    )
    deps["client"].chat_postMessage.assert_awaited_once_with(channel="U1", text="⏰ Time's up")
