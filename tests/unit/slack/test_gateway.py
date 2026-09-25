from typing import Any

import pytest
from slack_sdk.errors import SlackApiError

from jargonator.slack.gateway import BoltSlackGateway, MessageRef, SlackDeliveryError


class StubResponse(dict[str, Any]):
    pass


class StubWebClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.errors: dict[str, str] = {}
        self.admins: set[str] = set()

    def _call(self, method: str, **kwargs: Any) -> StubResponse:
        self.calls.append((method, kwargs))
        if method in self.errors:
            raise SlackApiError("boom", StubResponse(ok=False, error=self.errors[method]))
        return StubResponse(ok=True)

    async def chat_postMessage(self, **kwargs: Any) -> StubResponse:
        self._call("chat_postMessage", **kwargs)
        return StubResponse(ok=True, channel=kwargs["channel"], ts="123.456")

    async def chat_update(self, **kwargs: Any) -> StubResponse:
        return self._call("chat_update", **kwargs)

    async def chat_postEphemeral(self, **kwargs: Any) -> StubResponse:
        return self._call("chat_postEphemeral", **kwargs)

    async def conversations_open(self, **kwargs: Any) -> StubResponse:
        self._call("conversations_open", **kwargs)
        return StubResponse(ok=True, channel={"id": f"D-{kwargs['users']}"})

    async def users_info(self, **kwargs: Any) -> StubResponse:
        self._call("users_info", **kwargs)
        user = kwargs["user"]
        return StubResponse(
            ok=True, user={"id": user, "is_admin": user in self.admins, "is_owner": False}
        )


def make() -> tuple[BoltSlackGateway, StubWebClient]:
    client = StubWebClient()
    return BoltSlackGateway(client), client  # type: ignore[arg-type]


async def test_post_update_ephemeral() -> None:
    gateway, client = make()
    ref = await gateway.post_message("C1", "hi", [{"type": "divider"}], thread_ts="1.1")
    assert ref == MessageRef("C1", "123.456")
    assert client.calls[0] == (
        "chat_postMessage",
        {"channel": "C1", "text": "hi", "blocks": [{"type": "divider"}], "thread_ts": "1.1"},
    )
    await gateway.update_message(ref, "edited", [])
    await gateway.post_ephemeral("C1", "U1", "psst")
    assert [c[0] for c in client.calls] == ["chat_postMessage", "chat_update", "chat_postEphemeral"]


async def test_open_dm_is_cached() -> None:
    gateway, client = make()
    assert await gateway.open_dm("U1") == "D-U1"
    assert await gateway.open_dm("U1") == "D-U1"
    assert [c[0] for c in client.calls] == ["conversations_open"]


async def test_admin_check_is_cached() -> None:
    gateway, client = make()
    client.admins.add("UA")
    assert await gateway.is_workspace_admin("UA") is True
    assert await gateway.is_workspace_admin("UA") is True
    assert await gateway.is_workspace_admin("U1") is False
    assert [c[0] for c in client.calls] == ["users_info", "users_info"]


@pytest.mark.parametrize("code", ["not_in_channel", "channel_not_found", "cannot_dm_bot"])
async def test_errors_are_mapped(code: str) -> None:
    gateway, client = make()
    client.errors["chat_postMessage"] = code
    with pytest.raises(SlackDeliveryError) as info:
        await gateway.post_message("C1", "hi", [])
    assert info.value.code == code
