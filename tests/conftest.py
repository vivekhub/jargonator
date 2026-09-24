import pytest

REQUIRED_ENV = {
    "SLACK_BOT_TOKEN": "xoxb-test",
    "SLACK_APP_TOKEN": "xapp-test",
    "OPENROUTER_API_KEY": "sk-or-test",
}


@pytest.fixture
def required_env(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """Set only the required env vars and clear any optional overrides."""
    from jargonator.config import Settings

    for name in Settings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    return dict(REQUIRED_ENV)
