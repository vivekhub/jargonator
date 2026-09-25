from jargonator.slack import ids
from jargonator.slack.blocks import host_changed_notice, inactive_dm, skipped_round
from tests.snapshot import assert_snapshot


def test_skipped_round_snapshot() -> None:
    text, blocks = skipped_round(4, "g1", "r1", "⏭️ <@U2> didn't submit in time.", "host")
    assert_snapshot("skipped_round", {"text": text, "blocks": blocks})
    assert ids.NEXT in str(blocks) and "didn't submit" in text


def test_skipped_round_with_claim_and_without_controls() -> None:
    _, claim = skipped_round(4, "g1", "r1", "x", "claim")
    assert ids.CLAIM_HOST in str(claim)
    _, none = skipped_round(4, "g1", "r1", "x", "none")
    assert "action_id" not in str(none)


def test_inactive_dm_snapshot() -> None:
    text, blocks = inactive_dm()
    assert_snapshot("inactive_dm", {"text": text, "blocks": blocks})
    assert "Join" in text


def test_host_changed_notice() -> None:
    text, _ = host_changed_notice("U3")
    assert text == "👑 <@U3> is now the host."
