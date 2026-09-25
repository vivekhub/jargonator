"""Keeps configuration docs and the Slack manifest in sync with the code."""

import re
from pathlib import Path

import yaml

from jargonator.config import Settings
from jargonator.slack import ids

ROOT = Path(__file__).resolve().parents[2]
SETTING_NAMES = sorted(name.upper() for name in Settings.model_fields)


def test_every_setting_is_in_env_example() -> None:
    text = (ROOT / ".env.example").read_text()
    missing = [n for n in SETTING_NAMES if not re.search(rf"^#?\s*{n}=", text, re.MULTILINE)]
    assert missing == []


def test_every_setting_is_in_the_readme_config_table() -> None:
    text = (ROOT / "README.md").read_text()
    assert [n for n in SETTING_NAMES if f"| `{n}` |" not in text] == []


def test_manifest_matches_the_app() -> None:
    manifest = yaml.safe_load((ROOT / "slack-manifest.yaml").read_text())
    assert manifest["settings"]["socket_mode_enabled"] is True
    assert manifest["settings"]["interactivity"]["is_enabled"] is True
    [command] = manifest["features"]["slash_commands"]
    assert command["command"] == "/jargonator"
    assert command["should_escape"] is True  # we parse <@U123|name> mentions for kick
    assert set(manifest["oauth_config"]["scopes"]["bot"]) == {
        "commands", "chat:write", "im:write", "users:read", "channels:read", "groups:read",
    }  # fmt: skip


def test_readme_maps_every_acceptance_criterion_to_a_test() -> None:
    text = (ROOT / "README.md").read_text()
    for number in range(1, 11):
        match = re.search(rf"^\| {number} \| .* \| (`tests/[^`]+`)", text, re.MULTILINE)
        assert match, f"acceptance criterion {number} not mapped"
        for path in re.findall(r"`(tests/[^`]+\.py)`", match.group(0)):
            assert (ROOT / path).exists(), path


def test_all_action_ids_are_handled() -> None:
    from jargonator.slack.actions import CHANNEL_BUTTONS, MODAL_BUTTONS

    buttons = {v for k, v in vars(ids).items() if k.isupper() and not k.endswith("_MODAL")}
    assert buttons == CHANNEL_BUTTONS | MODAL_BUTTONS
