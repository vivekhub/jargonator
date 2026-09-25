"""Drives the game the way Slack would: through the real command, button and modal
handlers, with a real engine, real SQLite and real TimerService on a fake clock."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any
from unittest.mock import AsyncMock

from jargonator.slack import actions, ids
from jargonator.slack.commands import handle_jargonator
from tests.engine_harness import Harness


async def wait_until(check: Callable[[], Awaitable[bool]]) -> None:
    async with asyncio.timeout(5):
        while not await check():  # noqa: ASYNC110 (test helper: polling is the point)
            await asyncio.sleep(0.01)


class SlackDriver:
    def __init__(self, harness: Harness) -> None:
        self.h = harness
        self.client = AsyncMock()
        self.responses: dict[str, list[str]] = {}

    def _respond(self, user: str) -> AsyncMock:
        async def respond(**kwargs: Any) -> None:
            self.responses.setdefault(user, []).append(str(kwargs.get("text", "")))

        return AsyncMock(side_effect=respond)

    def last_response(self, user: str) -> str:
        return self.responses.get(user, [""])[-1]

    async def command(self, user: str, text: str, channel: str = "C1") -> None:
        await handle_jargonator(
            ack=AsyncMock(),
            command={"text": text, "user_id": user, "channel_id": channel, "trigger_id": "t"},
            respond=self._respond(user),
            client=self.client,
            engine=self.h.engine,
            settings=self.h.settings,
        )

    async def click(self, user: str, action_id: str, value: str, channel: str = "C1") -> None:
        await actions.handle_button(
            ack=AsyncMock(),
            body={
                "user": {"id": user},
                "channel": {"id": channel},
                "trigger_id": "t",
                "actions": [{"action_id": action_id, "value": value}],
            },
            respond=self._respond(user),
            client=self.client,
            engine=self.h.engine,
        )

    def opened_view(self) -> dict[str, Any]:
        view: dict[str, Any] = self.client.views_open.await_args.kwargs["view"]
        return view

    async def submit_start(self, user: str, guess: int, writer: int, join: int) -> None:
        view = self.opened_view()
        assert view["callback_id"] == ids.START_MODAL
        view = {
            **view,
            "state": {
                "values": {
                    "guess_seconds": {"value": {"value": str(guess)}},
                    "writer_seconds": {"value": {"value": str(writer)}},
                    "join_window_seconds": {"value": {"value": str(join)}},
                }
            },
        }
        await actions.handle_start_modal(
            ack=AsyncMock(),
            body={"user": {"id": user}},
            view=view,
            client=self.client,
            engine=self.h.engine,
            settings=self.h.settings,
        )
        created = await self.h.repo.get_active_game_by_channel(self.h.channel)
        if created is not None:  # lets Harness.game() find it even after it ends
            self.h.extra.setdefault("game_ids", []).append(created.id)

    async def submit_text(self, user: str, text: str) -> None:
        view = self.opened_view()
        block_id = "sentence" if view["callback_id"] == ids.SENTENCE_MODAL else "guess"
        handler = (
            actions.handle_sentence_modal
            if view["callback_id"] == ids.SENTENCE_MODAL
            else actions.handle_guess_modal
        )
        view = {**view, "state": {"values": {block_id: {"value": {"value": text}}}}}
        await handler(
            ack=AsyncMock(),
            body={"user": {"id": user}},
            view=view,
            client=self.client,
            engine=self.h.engine,
        )

    # --- higher-level moves --------------------------------------------------------------

    async def write_sentence(self, text: str) -> str:
        rnd = await self.h.round()
        await self.click(
            rnd.writer_user_id, ids.WRITE_SENTENCE, rnd.id, channel=f"D{rnd.writer_user_id}"
        )
        await self.submit_text(rnd.writer_user_id, text)
        return rnd.writer_user_id

    async def guess(self, user: str, text: str) -> None:
        rnd = await self.h.round()
        await self.click(user, ids.SUBMIT_GUESS, rnd.id, channel=f"D{user}")
        await self.submit_text(user, text)
