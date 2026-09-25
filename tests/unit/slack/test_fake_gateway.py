import pytest

from jargonator.slack.gateway import MessageRef, SlackDeliveryError, SlackGateway
from tests.fakes.slack import FakeSlackGateway

BLOCKS = [{"type": "section", "text": {"type": "mrkdwn", "text": "hi"}}]


async def test_post_and_update_track_current_state() -> None:
    slack = FakeSlackGateway()
    ref = await slack.post_message("C1", "hi", BLOCKS)
    assert slack.current(ref).text == "hi"
    await slack.update_message(ref, "updated", [])
    assert slack.current(ref).text == "updated"
    assert slack.current(ref).blocks == []
    assert [m.text for m in slack.messages_in("C1")] == ["updated"]


async def test_ts_values_are_unique_and_increasing() -> None:
    slack = FakeSlackGateway()
    refs = [await slack.post_message("C1", str(i), []) for i in range(5)]
    ts = [float(r.ts) for r in refs]
    assert ts == sorted(ts) and len(set(ts)) == 5


async def test_threads_and_dms() -> None:
    slack = FakeSlackGateway()
    parent = await slack.post_message("C1", "parent", [])
    await slack.post_message("C1", "reply", [], thread_ts=parent.ts)
    dm = await slack.open_dm("U2")
    await slack.post_message(dm, "psst", [])
    assert dm == await slack.open_dm("U2")
    assert [m.text for m in slack.messages_in("C1")] == ["parent", "reply"]
    assert slack.messages_in("C1")[1].thread_ts == parent.ts
    assert [m.text for m in slack.dms_to("U2")] == ["psst"]


async def test_ephemerals() -> None:
    slack = FakeSlackGateway()
    await slack.post_ephemeral("C1", "U3", "only you")
    assert slack.ephemerals_to("U3") == ["only you"]


async def test_admins() -> None:
    slack = FakeSlackGateway(admins={"UADMIN"})
    assert await slack.is_workspace_admin("UADMIN")
    assert not await slack.is_workspace_admin("U1")


async def test_injected_channel_failure() -> None:
    slack = FakeSlackGateway()
    slack.fail_channel("C9", "not_in_channel")
    with pytest.raises(SlackDeliveryError) as info:
        await slack.post_message("C9", "x", [])
    assert info.value.code == "not_in_channel"


async def test_injected_dm_failure() -> None:
    slack = FakeSlackGateway()
    slack.fail_dm("UBOT", "cannot_dm_bot")
    with pytest.raises(SlackDeliveryError):
        await slack.open_dm("UBOT")


async def test_update_unknown_message_raises() -> None:
    slack = FakeSlackGateway()
    with pytest.raises(SlackDeliveryError):
        await slack.update_message(MessageRef("C1", "999.9"), "x", [])


async def test_call_log_records_order() -> None:
    slack = FakeSlackGateway()
    ref = await slack.post_message("C1", "a", [])
    await slack.update_message(ref, "b", [])
    assert [c[0] for c in slack.calls] == ["post_message", "update_message"]


def test_satisfies_protocol() -> None:
    gateway: SlackGateway = FakeSlackGateway()  # also checked by mypy
    assert gateway is not None
