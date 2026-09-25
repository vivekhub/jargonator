"""How the engine talks to Slack, as a protocol (spec.md §6.3).

The engine depends only on ``SlackGateway``. The real Bolt-backed implementation arrives
in Prompt 21, and tests use ``tests/fakes/slack.FakeSlackGateway``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

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
