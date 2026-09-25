"""How the engine talks to Slack (spec.md §6.3).

The engine depends only on the ``SlackGateway`` protocol. ``BoltSlackGateway`` is the real
implementation over slack_sdk's ``AsyncWebClient``, and tests use
``tests/fakes/slack.FakeSlackGateway``.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from slack_sdk.errors import SlackApiError
from slack_sdk.http_retry.builtin_async_handlers import AsyncRateLimitErrorRetryHandler
from slack_sdk.web.async_client import AsyncWebClient

Block = dict[str, Any]


@dataclass(frozen=True)
class MessageRef:
    """Where a message lives, so it can be updated later."""

    channel: str
    ts: str


class SlackDeliveryError(Exception):
    """Slack refused a call (e.g. ``not_in_channel``, ``channel_not_found``, ``cannot_dm_bot``)."""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code


class SlackGateway(Protocol):
    async def post_message(
        self, channel: str, text: str, blocks: Sequence[Block], thread_ts: str | None = None
    ) -> MessageRef:
        """Post a message. ``text`` is the notification/accessibility fallback."""
        ...

    async def update_message(self, ref: MessageRef, text: str, blocks: Sequence[Block]) -> None: ...

    async def post_ephemeral(
        self, channel: str, user: str, text: str, blocks: Sequence[Block] | None = None
    ) -> None: ...

    async def open_dm(self, user: str) -> str:
        """Return the DM channel id for ``user``."""
        ...

    async def is_workspace_admin(self, user: str) -> bool: ...


ADMIN_CACHE_SECONDS = 600


def _code(exc: SlackApiError) -> str:
    response: Any = exc.response
    try:
        return str(response.get("error") or "unknown_error")
    except AttributeError:
        return "unknown_error"


class BoltSlackGateway:
    """``SlackGateway`` over ``AsyncWebClient``. Slack errors become ``SlackDeliveryError``."""

    def __init__(self, client: AsyncWebClient) -> None:
        self._client = client
        self._dm_channels: dict[str, str] = {}
        self._admins: dict[str, tuple[bool, float]] = {}

    async def post_message(
        self, channel: str, text: str, blocks: Sequence[Block], thread_ts: str | None = None
    ) -> MessageRef:
        try:
            response = await self._client.chat_postMessage(
                channel=channel, text=text, blocks=list(blocks), thread_ts=thread_ts
            )
        except SlackApiError as exc:
            raise SlackDeliveryError(_code(exc)) from exc
        return MessageRef(str(response["channel"]), str(response["ts"]))

    async def update_message(self, ref: MessageRef, text: str, blocks: Sequence[Block]) -> None:
        try:
            await self._client.chat_update(
                channel=ref.channel, ts=ref.ts, text=text, blocks=list(blocks)
            )
        except SlackApiError as exc:
            raise SlackDeliveryError(_code(exc)) from exc

    async def post_ephemeral(
        self, channel: str, user: str, text: str, blocks: Sequence[Block] | None = None
    ) -> None:
        try:
            await self._client.chat_postEphemeral(
                channel=channel, user=user, text=text, blocks=list(blocks) if blocks else None
            )
        except SlackApiError as exc:
            raise SlackDeliveryError(_code(exc)) from exc

    async def open_dm(self, user: str) -> str:
        if user not in self._dm_channels:
            try:
                response = await self._client.conversations_open(users=user)
            except SlackApiError as exc:
                raise SlackDeliveryError(_code(exc)) from exc
            self._dm_channels[user] = str(response["channel"]["id"])
        return self._dm_channels[user]

    async def is_workspace_admin(self, user: str) -> bool:
        cached = self._admins.get(user)
        if cached is not None and time.monotonic() - cached[1] < ADMIN_CACHE_SECONDS:
            return cached[0]
        try:
            response = await self._client.users_info(user=user)
        except SlackApiError as exc:
            raise SlackDeliveryError(_code(exc)) from exc
        info = response["user"]
        is_admin = bool(info.get("is_admin") or info.get("is_owner"))
        self._admins[user] = (is_admin, time.monotonic())
        return is_admin


def build_web_client(bot_token: str) -> AsyncWebClient:
    """Web client with retries on Slack rate limits (HTTP 429)."""
    return AsyncWebClient(
        token=bot_token, retry_handlers=[AsyncRateLimitErrorRetryHandler(max_retry_count=2)]
    )
