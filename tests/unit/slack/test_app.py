from typing import Any
from unittest.mock import AsyncMock

from slack_bolt.authorization import AuthorizeResult
from slack_bolt.request.async_request import AsyncBoltRequest
from slack_sdk.web.async_client import AsyncWebClient

from jargonator.slack.app import build_bolt_app
from tests.engine_harness import make_settings


async def fake_authorize(**_: Any) -> AuthorizeResult:
    return AuthorizeResult(
        enterprise_id=None, team_id="T1", bot_token="xoxb-x", bot_user_id="UBOT", bot_id="B1"
    )


async def test_slash_command_reaches_the_engine(required_env: dict[str, str]) -> None:
    engine = AsyncMock()
    app = build_bolt_app(
        make_settings(), engine, AsyncWebClient(token="xoxb-x"), authorize=fake_authorize
    )
    body = {
        "command": "/jargonator",
        "text": "join",
        "user_id": "U1",
        "channel_id": "C1",
        "team_id": "T1",
        "trigger_id": "trig",
        "response_url": "https://hooks.slack.com/commands/x",
        "token": "t",
    }
    response = await app.async_dispatch(AsyncBoltRequest(body=body, mode="socket_mode"))
    assert response.status == 200
    engine.join.assert_awaited_once_with("C1", "U1")
