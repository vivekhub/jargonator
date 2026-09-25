from datetime import UTC, datetime

from jargonator.domain.state import JargonLevel
from jargonator.slack import ids
from jargonator.slack.blocks import (
    escape,
    guess_prompt,
    guess_prompt_submitted,
    jargon_out,
    llm_retry_notice,
)
from tests.snapshot import assert_snapshot

DEADLINE = datetime(2026, 3, 2, 10, 3, tzinfo=UTC)
JARGON = "I steward a dual-asset feline stakeholder portfolio."


def test_escape_neutralises_mentions_and_links() -> None:
    assert escape("<!channel> & <@U1>") == "&lt;!channel&gt; &amp; &lt;@U1&gt;"


def test_jargon_out_snapshot_and_never_contains_jargon() -> None:
    text, blocks = jargon_out(2, "U2", 1, 3, DEADLINE)
    assert_snapshot("jargon_out", {"text": text, "blocks": blocks})
    assert "1/3 guessed" in text
    assert "feline" not in str(blocks) and "feline" not in text


def test_guess_prompt_snapshot() -> None:
    text, blocks = guess_prompt(2, JargonLevel.SPICY, JARGON, DEADLINE, "round-9")
    assert_snapshot("guess_prompt", {"text": text, "blocks": blocks})
    assert "Spicy" in str(blocks) and JARGON in str(blocks)
    buttons = [e for b in blocks if b["type"] == "actions" for e in b["elements"]]
    assert [(b["action_id"], b["value"]) for b in buttons] == [(ids.SUBMIT_GUESS, "round-9")]


def test_guess_prompt_escapes_jargon() -> None:
    _, blocks = guess_prompt(2, JargonLevel.MILD, "<!here> synergy", DEADLINE, "r")
    assert "<!here>" not in str(blocks) and "&lt;!here&gt;" in str(blocks)


def test_guess_prompt_submitted_snapshot() -> None:
    text, blocks = guess_prompt_submitted(2, JargonLevel.UNHINGED, JARGON, "I have <cats>")
    assert_snapshot("guess_prompt_submitted", {"text": text, "blocks": blocks})
    assert ids.SUBMIT_GUESS not in str(blocks)
    assert "I have &lt;cats&gt;" in str(blocks)


def test_llm_retry_notice() -> None:
    text, _ = llm_retry_notice(30)
    assert "retrying in 30 seconds" in text
