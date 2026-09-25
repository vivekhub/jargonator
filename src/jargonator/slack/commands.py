"""The /jargonator slash command (spec.md §4): parse, ack, call the engine."""

import re
from typing import Any

import structlog

from jargonator.config import Settings
from jargonator.engine.errors import UserFacingError
from jargonator.engine.game_engine import INVITE_HINT, NOT_IN_CHANNEL_CODES, GameEngine
from jargonator.slack import blocks, views
from jargonator.slack.gateway import SlackDeliveryError

_MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")

log = structlog.get_logger()


def parse_mention(text: str) -> str | None:
    match = _MENTION.search(text)
    return match.group(1) if match else None


async def handle_jargonator(
    *,
    ack: Any,
    command: dict[str, Any],
    respond: Any,
    client: Any,
    engine: GameEngine,
    settings: Settings,
) -> None:
    await ack()  # always within Slack's 3 s limit, before any work
    channel_id = str(command.get("channel_id", ""))
    user_id = str(command.get("user_id", ""))
    subcommand, _, args = str(command.get("text", "")).strip().partition(" ")
    subcommand = subcommand.lower()
    structlog.contextvars.bind_contextvars(channel_id=channel_id, user_id=user_id)

    async def say(text: str, message_blocks: list[Any] | None = None) -> None:
        kwargs: dict[str, Any] = {"text": text, "response_type": "ephemeral"}
        if message_blocks is not None:
            kwargs["blocks"] = message_blocks
        await respond(**kwargs)

    if subcommand in ("", "help"):
        await say(*blocks.help_text())
        return
    if channel_id.startswith("D"):
        await say("Use `/jargonator` in a channel, so everyone there can play.")
        return
    try:
        if subcommand == "start":
            view = await engine.get_status(channel_id)
            if view is not None:
                text, status_blocks = blocks.status(view, engine.clock.now())
                await say("There's already a game running here. " + text, status_blocks)
                return
            await client.views_open(
                trigger_id=command["trigger_id"], view=views.start_modal(settings, channel_id)
            )
        elif subcommand == "join":
            await engine.join(channel_id, user_id)
            await say("You're in! 🎉")
        elif subcommand == "leave":
            await engine.leave(channel_id, user_id)
            await say("You've left the game. Your score is kept if you come back.")
        elif subcommand == "next":
            await engine.next_round(channel_id, user_id)
        elif subcommand == "end":
            await engine.end_game(channel_id, user_id)
        elif subcommand == "kick":
            target = parse_mention(args)
            if target is None:
                await say("Usage: `/jargonator kick @someone`")
                return
            await engine.kick(channel_id, user_id, target)
        elif subcommand == "status":
            view = await engine.get_status(channel_id)
            if view is None:
                await say("No game running here. Start one with `/jargonator start`.")
                return
            await say(*blocks.status(view, engine.clock.now()))
        else:
            await say(*blocks.help_text())
    except UserFacingError as exc:
        await say(exc.message)
    except SlackDeliveryError as exc:
        if exc.code in NOT_IN_CHANNEL_CODES:
            await say(INVITE_HINT)
        else:
            log.warning("command_slack_error", code=exc.code, subcommand=subcommand)
            await say("Slack refused that just now. Please try again.")
