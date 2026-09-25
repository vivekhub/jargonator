"""Button and modal handlers (spec.md §6.1, §6.2). Every handler acks first (Slack's 3 s
limit), then calls the engine. Engine refusals are shown privately to the clicker."""

from typing import Any

import structlog
from slack_bolt.async_app import AsyncApp
from slack_sdk.errors import SlackApiError

from jargonator.config import Settings
from jargonator.engine.errors import UserFacingError
from jargonator.engine.game_engine import (
    INVITE_HINT,
    MAX_GUESS,
    MAX_SENTENCE,
    MIN_GUESS,
    MIN_SENTENCE,
    NOT_IN_CHANNEL_CODES,
    GameEngine,
)
from jargonator.slack import ids, views
from jargonator.slack.gateway import SlackDeliveryError

log = structlog.get_logger()

CHANNEL_BUTTONS = {ids.JOIN, ids.START, ids.NEXT, ids.END, ids.CLAIM_HOST}
MODAL_BUTTONS = {ids.WRITE_SENTENCE, ids.TRY_AGAIN, ids.SUBMIT_GUESS}


async def handle_button(
    *, ack: Any, body: dict[str, Any], respond: Any, client: Any, engine: GameEngine
) -> None:
    await ack()
    action = body["actions"][0]
    action_id, value = action["action_id"], str(action.get("value", ""))
    user_id = body["user"]["id"]
    channel_id = body.get("channel", {}).get("id", "")
    structlog.contextvars.bind_contextvars(user_id=user_id, channel_id=channel_id)
    try:
        if action_id == ids.JOIN:
            await engine.join(channel_id, user_id)
        elif action_id == ids.START:
            await engine.start_game(channel_id, user_id)
        elif action_id == ids.NEXT:
            await engine.next_round(channel_id, user_id)
        elif action_id == ids.END:
            await engine.end_game(channel_id, user_id)
        elif action_id == ids.CLAIM_HOST:
            await engine.claim_host(channel_id, user_id, value)
        elif action_id in (ids.WRITE_SENTENCE, ids.TRY_AGAIN):
            await engine.check_can_write(user_id, value)
            await client.views_open(trigger_id=body["trigger_id"], view=views.sentence_modal(value))
        elif action_id == ids.SUBMIT_GUESS:
            round_no, level, jargon = await engine.guess_context(user_id, value)
            await client.views_open(
                trigger_id=body["trigger_id"],
                view=views.guess_modal(value, round_no, level, jargon),
            )
    except UserFacingError as exc:
        await respond(text=exc.message, response_type="ephemeral", replace_original=False)
    except SlackDeliveryError as exc:
        text = INVITE_HINT if exc.code in NOT_IN_CHANNEL_CODES else "Slack refused that. Try again."
        await respond(text=text, response_type="ephemeral", replace_original=False)


async def handle_start_modal(
    *,
    ack: Any,
    body: dict[str, Any],
    view: dict[str, Any],
    client: Any,
    engine: GameEngine,
    settings: Settings,
) -> None:
    parsed = views.parse_start_submission(view)
    if isinstance(parsed, dict):
        await ack(response_action="errors", errors=parsed)
        return
    await ack()
    user_id = body["user"]["id"]
    structlog.contextvars.bind_contextvars(user_id=user_id, channel_id=parsed.channel_id)
    try:
        await engine.create_game(
            channel_id=parsed.channel_id,
            user_id=user_id,
            guess_seconds=parsed.guess_seconds,
            writer_seconds=parsed.writer_seconds,
            join_window_seconds=parsed.join_window_seconds,
        )
    except UserFacingError as exc:
        await _tell(client, user_id, exc.message, channel_id=parsed.channel_id)


async def handle_sentence_modal(
    *, ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any, engine: GameEngine
) -> None:
    parsed = views.parse_text_submission(view, views.SENTENCE_BLOCK, MIN_SENTENCE, MAX_SENTENCE)
    if isinstance(parsed, dict):
        await ack(response_action="errors", errors=parsed)
        return
    await ack()  # closes the modal; moderation and generation continue below
    user_id = body["user"]["id"]
    structlog.contextvars.bind_contextvars(user_id=user_id)
    try:
        await engine.submit_sentence(user_id, str(view.get("private_metadata", "")), parsed)
    except UserFacingError as exc:
        await _tell(client, user_id, exc.message)


async def handle_guess_modal(
    *, ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any, engine: GameEngine
) -> None:
    parsed = views.parse_text_submission(view, views.GUESS_BLOCK, MIN_GUESS, MAX_GUESS)
    if isinstance(parsed, dict):
        await ack(response_action="errors", errors=parsed)
        return
    await ack()
    user_id = body["user"]["id"]
    structlog.contextvars.bind_contextvars(user_id=user_id)
    try:
        await engine.submit_guess(user_id, str(view.get("private_metadata", "")), parsed)
    except UserFacingError as exc:
        await _tell(client, user_id, exc.message)


async def _tell(client: Any, user_id: str, text: str, *, channel_id: str | None = None) -> None:
    """Tell one user something privately: ephemerally in ``channel_id`` if given (falling
    back to a DM, e.g. when the bot isn't in that channel), otherwise by DM."""
    if channel_id:
        try:
            await client.chat_postEphemeral(channel=channel_id, user=user_id, text=text)
            return
        except SlackApiError:
            log.info("ephemeral_failed_falling_back_to_dm", user_id=user_id)
    try:
        await client.chat_postMessage(channel=user_id, text=text)
    except SlackApiError:
        log.warning("could_not_notify_user", user_id=user_id)


def register(app: AsyncApp, engine: GameEngine, settings: Settings) -> None:
    async def _button(ack: Any, body: dict[str, Any], respond: Any, client: Any) -> None:
        await handle_button(ack=ack, body=body, respond=respond, client=client, engine=engine)

    for action_id in sorted(CHANNEL_BUTTONS | MODAL_BUTTONS):
        app.action(action_id)(_button)

    @app.view(ids.START_MODAL)
    async def _start(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        await handle_start_modal(
            ack=ack, body=body, view=view, client=client, engine=engine, settings=settings
        )

    @app.view(ids.SENTENCE_MODAL)
    async def _sentence(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        await handle_sentence_modal(ack=ack, body=body, view=view, client=client, engine=engine)

    @app.view(ids.GUESS_MODAL)
    async def _guess(ack: Any, body: dict[str, Any], view: dict[str, Any], client: Any) -> None:
        await handle_guess_modal(ack=ack, body=body, view=view, client=client, engine=engine)
