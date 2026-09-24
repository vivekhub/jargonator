import pytest
from pydantic import ValidationError

from jargonator.config import Settings


def make_settings() -> Settings:
    return Settings(_env_file=None)  # type: ignore[call-arg]


def test_defaults_match_spec(required_env: dict[str, str]) -> None:
    s = make_settings()
    assert s.slack_bot_token.get_secret_value() == "xoxb-test"
    assert s.slack_app_token.get_secret_value() == "xapp-test"
    assert s.openrouter_api_key.get_secret_value() == "sk-or-test"
    assert s.llm_model_jargon == "anthropic/claude-sonnet-5"
    assert s.llm_model_quip == "anthropic/claude-sonnet-5"
    assert s.llm_model_moderation == "anthropic/claude-haiku-4.5"
    assert s.llm_model_tiebreak == "anthropic/claude-haiku-4.5"
    assert s.llm_model_fallback == "openai/gpt-4o-mini"
    assert s.llm_timeout_seconds == 15
    assert s.embedding_model == "sentence-transformers/all-MiniLM-L6-v2"
    assert s.database_url == "sqlite+aiosqlite:////data/jargonator.db"
    assert (s.points_first, s.points_second, s.points_third) == (10, 5, 1)
    assert s.writer_bonus_points == 10
    assert s.writer_bonus_threshold == 0.5
    assert s.tie_margin == 0.02
    assert s.default_guess_seconds == 60
    assert s.default_writer_seconds == 90
    assert s.default_join_window_seconds == 120
    assert s.writer_reminder_seconds == 30
    assert s.max_consecutive_misses == 3
    assert s.host_claim_after_seconds == 300
    assert s.idle_timeout_seconds == 7200
    assert s.min_players == 2
    assert s.log_level == "INFO"
    assert s.health_port == 8080


def test_points_by_rank_default(required_env: dict[str, str]) -> None:
    assert make_settings().points_by_rank == (10, 5, 1)


@pytest.mark.parametrize("missing", ["SLACK_BOT_TOKEN", "SLACK_APP_TOKEN", "OPENROUTER_API_KEY"])
def test_missing_required_var_raises(
    required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(ValidationError):
        make_settings()


def test_env_overrides(required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("POINTS_FIRST", "20")
    monkeypatch.setenv("TIE_MARGIN", "0.05")
    monkeypatch.setenv("DEFAULT_JOIN_WINDOW_SECONDS", "0")
    monkeypatch.setenv("LLM_MODEL_JARGON", "openai/gpt-5")
    s = make_settings()
    assert s.points_by_rank == (20, 5, 1)
    assert s.tie_margin == 0.05
    assert s.default_join_window_seconds == 0
    assert s.llm_model_jargon == "openai/gpt-5"


@pytest.mark.parametrize(
    ("var", "value"),
    [
        ("WRITER_BONUS_THRESHOLD", "1.5"),
        ("WRITER_BONUS_THRESHOLD", "-0.1"),
        ("TIE_MARGIN", "2"),
        ("DEFAULT_GUESS_SECONDS", "-5"),
        ("DEFAULT_WRITER_SECONDS", "0"),
        ("DEFAULT_JOIN_WINDOW_SECONDS", "-1"),
        ("POINTS_THIRD", "0"),
        ("MIN_PLAYERS", "1"),
        ("IDLE_TIMEOUT_SECONDS", "0"),
    ],
)
def test_invalid_values_rejected(
    required_env: dict[str, str], monkeypatch: pytest.MonkeyPatch, var: str, value: str
) -> None:
    monkeypatch.setenv(var, value)
    with pytest.raises(ValidationError):
        make_settings()


def test_secrets_not_exposed_in_repr(required_env: dict[str, str]) -> None:
    assert "xoxb-test" not in repr(make_settings())
