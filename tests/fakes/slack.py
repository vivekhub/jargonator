"""In-memory SlackGateway that records everything, for engine and adapter tests."""

import asyncio
import itertools
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from jargonator.slack.gateway import Block, MessageRef, SlackDeliveryError

if TYPE_CHECKING:
    from jargonator.slack.gateway import SlackGateway


@dataclass
class FakeMessage:
    ref: MessageRef
    text: str
    blocks: list[Block]
    thread_ts: str | None = None
    history: list[tuple[str, list[Block]]] = field(default_factory=list)
    """Previous (text, blocks) versions, oldest first."""


class FakeSlackGateway:
    def __init__(self, admins: Iterable[str] = ()) -> None:
        self.admins = set(admins)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.messages: dict[MessageRef, FakeMessage] = {}
        self.ephemerals: list[tuple[str, str, str]] = []
        """(channel, user, text)"""
        self._ts = itertools.count(1)
        self._channel_failures: dict[str, str] = {}
        self._dm_failures: dict[str, str] = {}
        self.in_flight = 0
        self.max_in_flight = 0
        """Peak number of overlapping ``open_dm``/``post_message`` calls."""
        self.on_open_dm: Callable[[str], None] | None = None
        """Called with the user inside every ``open_dm`` (e.g. to advance a fake clock)."""

    # --- failure injection --------------------------------------------------------------

    def fail_channel(self, channel: str, code: str) -> None:
        self._channel_failures[channel] = code

    def fail_dm(self, user: str, code: str) -> None:
        self._dm_failures[user] = code

    def _check(self, channel: str) -> None:
        if channel in self._channel_failures:
            raise SlackDeliveryError(self._channel_failures[channel])

    async def _overlap(self) -> None:
        """Yield once while counted as in flight, so concurrent callers can overlap."""
        self.in_flight += 1
        self.max_in_flight = max(self.max_in_flight, self.in_flight)
        try:
            await asyncio.sleep(0)
        finally:
            self.in_flight -= 1

    # --- SlackGateway ---------------------------------------------------------------------

    async def post_message(
        self, channel: str, text: str, blocks: Sequence[Block], thread_ts: str | None = None
    ) -> MessageRef:
        self.calls.append(("post_message", {"channel": channel, "text": text}))
        await self._overlap()
        self._check(channel)
        ref = MessageRef(channel, f"{1000 + next(self._ts)}.000100")
        self.messages[ref] = FakeMessage(ref, text, list(blocks), thread_ts)
        return ref

    async def update_message(self, ref: MessageRef, text: str, blocks: Sequence[Block]) -> None:
        self.calls.append(("update_message", {"channel": ref.channel, "ts": ref.ts, "text": text}))
        self._check(ref.channel)
        message = self.messages.get(ref)
        if message is None:
            raise SlackDeliveryError("message_not_found")
        message.history.append((message.text, message.blocks))
        message.text, message.blocks = text, list(blocks)

    async def post_ephemeral(
        self, channel: str, user: str, text: str, blocks: Sequence[Block] | None = None
    ) -> None:
        self.calls.append(("post_ephemeral", {"channel": channel, "user": user, "text": text}))
        self.ephemerals.append((channel, user, text))

    async def open_dm(self, user: str) -> str:
        self.calls.append(("open_dm", {"user": user}))
        await self._overlap()
        if self.on_open_dm is not None:
            self.on_open_dm(user)
        if user in self._dm_failures:
            raise SlackDeliveryError(self._dm_failures[user])
        return f"D{user}"

    async def is_workspace_admin(self, user: str) -> bool:
        return user in self.admins

    # --- test helpers ---------------------------------------------------------------------

    def current(self, ref: MessageRef) -> FakeMessage:
        return self.messages[ref]

    def messages_in(self, channel: str) -> list[FakeMessage]:
        """All messages posted in ``channel`` (including thread replies), oldest first."""
        return [m for m in self.messages.values() if m.ref.channel == channel]

    def dms_to(self, user: str) -> list[FakeMessage]:
        return self.messages_in(f"D{user}")

    def ephemerals_to(self, user: str) -> list[str]:
        return [text for _, u, text in self.ephemerals if u == user]


if TYPE_CHECKING:
    _protocol_check: SlackGateway = FakeSlackGateway()
