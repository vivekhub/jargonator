from datetime import UTC, datetime

from jargonator.slack import ids
from jargonator.slack.blocks import (
    deadline_text,
    round_start,
    writer_accepted,
    writer_prompt,
    writer_rejected,
    writer_reminder,
)
from tests.snapshot import assert_snapshot

DEADLINE = datetime(2026, 3, 2, 10, 1, 30, tzinfo=UTC)


def test_deadline_text_uses_slack_date_formatting() -> None:
    assert (
        deadline_text(DEADLINE) == f"<!date^{int(DEADLINE.timestamp())}^{{time_secs}}|10:01:30 UTC>"
    )


def test_round_start_snapshot() -> None:
    text, blocks = round_start(3, "U2", DEADLINE)
    assert_snapshot("round_start", {"text": text, "blocks": blocks})
    assert "Round 3" in text and "<@U2>" in text


def test_writer_prompt_snapshot() -> None:
    text, blocks = writer_prompt(3, DEADLINE, "round-9")
    assert_snapshot("writer_prompt", {"text": text, "blocks": blocks})
    buttons = [e for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert [(b["action_id"], b["value"]) for b in buttons] == [(ids.WRITE_SENTENCE, "round-9")]


def test_writer_rejected_snapshot() -> None:
    text, blocks = writer_rejected("Please keep it about you.", "round-9")
    assert_snapshot("writer_rejected", {"text": text, "blocks": blocks})
    assert "Please keep it about you." in text
    assert ids.TRY_AGAIN in str(blocks)


def test_writer_reminder_snapshot() -> None:
    text, blocks = writer_reminder(30, "round-9")
    assert_snapshot("writer_reminder", {"text": text, "blocks": blocks})
    assert "30 seconds" in text and ids.WRITE_SENTENCE in str(blocks)


def test_writer_accepted() -> None:
    text, _ = writer_accepted("I have two cats")
    assert "I have two cats" in text
