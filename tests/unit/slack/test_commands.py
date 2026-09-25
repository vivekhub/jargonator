from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from jargonator.engine.errors import UserFacingError
from jargonator.slack import ids
from jargonator.slack.commands import handle_jargonator, parse_mention
from jargonator.slack.gateway import SlackDeliveryError
from tests.engine_harness import make_settings


def command(text: str, channel: str = "C1") -> dict[str, Any]:
    return {"text": text, "user_id": "U1", "channel_id": channel, "trigger_id": "trig"}


class Calls:
    def __init__(self) -> None:
        self.order: list[str] = []


async def run(
    text: str, engine: Any = None, channel: str = "C1"
) -> tuple[Any, AsyncMock, AsyncMock, AsyncMock]:
    calls: list[str] = []
    ack = AsyncMock(side_effect=lambda *a, **k: calls.append("ack"))
    respond = AsyncMock()
    client = AsyncMock()
    engine = engine or AsyncMock()
    engine.get_status = AsyncMock(return_value=None)
    original = {
        name: getattr(engine, name) for name in ("join", "leave", "next_round", "end_game", "kick")
    }
    for name, method in original.items():

        async def tracked(*args: Any, _m: Any = method, _n: str = name, **kwargs: Any) -> Any:
            calls.append(_n)
            return await _m(*args, **kwargs)

        setattr(engine, name, tracked)
    await handle_jargonator(
        ack=ack,
        command=command(text, channel),
        respond=respond,
        client=client,
        engine=engine,
        settings=make_settings(),
    )
    engine.calls = calls
    return engine, ack, respond, client


@pytest.mark.parametrize(
    ("text", "method", "args"),
    [
        ("join", "join", ("C1", "U1")),
        ("leave", "leave", ("C1", "U1")),
        ("next", "next_round", ("C1", "U1")),
        ("end", "end_game", ("C1", "U1")),
        ("kick <@U7|bob>", "kick", ("C1", "U1", "U7")),
        ("  JOIN  ", "join", ("C1", "U1")),
    ],
)
async def test_routes_to_engine_after_ack(
    required_env: dict[str, str], text: str, method: str, args: tuple[str, ...]
) -> None:
    engine, ack, _, _ = await run(text)
    ack.assert_awaited_once()
    assert engine.calls[0] == "ack" and engine.calls[1] == method


async def test_help_and_unknown(required_env: dict[str, str]) -> None:
    for text in ("", "help", "dance"):
        _, _, respond, _ = await run(text)
        assert "/jargonator start" in str(respond.await_args.kwargs["blocks"])


async def test_status_without_game(required_env: dict[str, str]) -> None:
    _, _, respond, _ = await run("status")
    assert "No game" in respond.await_args.kwargs["text"]


async def test_kick_needs_a_mention(required_env: dict[str, str]) -> None:
    engine, _, respond, _ = await run("kick bob")
    assert "kick" not in engine.calls
    assert "Usage" in respond.await_args.kwargs["text"]


async def test_start_opens_modal_with_defaults(required_env: dict[str, str]) -> None:
    engine = AsyncMock()
    _, _, _, client = await run("start", engine)
    view = client.views_open.await_args.kwargs["view"]
    assert client.views_open.await_args.kwargs["trigger_id"] == "trig"
    assert view["callback_id"] == ids.START_MODAL
    assert view["private_metadata"] == "C1"
    assert "60" in str(view) and "90" in str(view) and "120" in str(view)


async def test_start_with_running_game_shows_status(required_env: dict[str, str]) -> None:
    from jargonator.engine.game_engine import StatusView
    from tests.unit.slack.test_blocks_status import GAME, NOW, PLAYERS

    engine = AsyncMock()
    calls: list[str] = []
    ack = AsyncMock()
    respond = AsyncMock()
    client = AsyncMock()
    engine.get_status = AsyncMock(
        return_value=StatusView(GAME, PLAYERS, None, None, None, None, [])
    )
    engine.clock = MagicMock()
    engine.clock.now.return_value = NOW
    await handle_jargonator(
        ack=ack,
        command=command("start"),
        respond=respond,
        client=client,
        engine=engine,
        settings=make_settings(),
    )
    client.views_open.assert_not_awaited()
    assert "already a game" in respond.await_args.kwargs["text"]
    assert calls == []


async def test_user_facing_error_is_ephemeral(required_env: dict[str, str]) -> None:
    engine = AsyncMock()
    engine.join = AsyncMock(side_effect=UserFacingError("Nope!"))
    _, _, respond, _ = await run("join", engine)
    assert respond.await_args.kwargs == {"text": "Nope!", "response_type": "ephemeral"}


async def test_not_in_channel_hint(required_env: dict[str, str]) -> None:
    engine = AsyncMock()
    engine.join = AsyncMock(side_effect=SlackDeliveryError("not_in_channel"))
    _, _, respond, _ = await run("join", engine)
    assert "/invite" in respond.await_args.kwargs["text"]


async def test_dm_usage_refused(required_env: dict[str, str]) -> None:
    engine, _, respond, _ = await run("join", channel="D123")
    assert "join" not in engine.calls
    assert "channel" in respond.await_args.kwargs["text"]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("<@U7|bob>", "U7"), ("<@W123>", "W123"), ("bob", None), ("", None)],
)
def test_parse_mention(text: str, expected: str | None) -> None:
    assert parse_mention(text) == expected
