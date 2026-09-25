"""Typed runtime configuration loaded from environment variables (spec.md §11)."""

from typing import Annotated

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PositiveInt = Annotated[int, Field(gt=0)]
JudgeScore = Annotated[int, Field(ge=0, le=100)]


_TASK_MODEL_FIELDS = (
    "llm_model_jargon",
    "llm_model_judge",
    "llm_model_quip",
    "llm_model_moderation",
)


class Settings(BaseSettings):
    """All tunables from spec.md §11. Env var names are the upper-cased field names."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Credentials (required)
    slack_bot_token: SecretStr
    slack_app_token: SecretStr
    openrouter_api_key: SecretStr

    # LLM (spec §7.1)
    llm_model: str | None = None
    """Optional shortcut: sets every per-task model below unless that one is set explicitly.
    The fallback is deliberately not affected, so it can stay on a different vendor."""
    llm_model_jargon: str = "anthropic/claude-sonnet-5"
    llm_model_judge: str = "anthropic/claude-sonnet-5"
    llm_model_quip: str = "anthropic/claude-sonnet-5"
    llm_model_moderation: str = "anthropic/claude-haiku-4.5"
    llm_model_fallback: str = "openai/gpt-4o-mini"
    llm_timeout_seconds: PositiveInt = 15
    llm_failure_retry_seconds: PositiveInt = 30
    """Wait before the single retry of an essential LLM step (spec §3.11)."""

    # Storage (spec §10)
    database_url: str = "sqlite+aiosqlite:////data/jargonator.db"

    # Scoring (spec §3.7, §8)
    points_first: PositiveInt = 10
    points_second: PositiveInt = 5
    points_third: PositiveInt = 1
    writer_bonus_points: PositiveInt = 10
    writer_bonus_threshold: JudgeScore = 50
    """The writer earns the bonus when the best judge score (0-100) is below this."""

    # Timing (spec §3.1, §3.4, §3.6, §3.9, §3.10)
    default_guess_seconds: PositiveInt = 60
    default_writer_seconds: PositiveInt = 90
    default_join_window_seconds: Annotated[int, Field(ge=0)] = 120
    writer_reminder_seconds: PositiveInt = 30
    max_consecutive_misses: PositiveInt = 3
    host_claim_after_seconds: PositiveInt = 300
    idle_timeout_seconds: PositiveInt = 7200
    min_players: Annotated[int, Field(ge=2)] = 2

    # Operations (spec §14)
    log_level: str = "INFO"
    health_port: Annotated[int, Field(gt=0, lt=65536)] = 8080

    @model_validator(mode="after")
    def _apply_llm_model(self) -> "Settings":
        if self.llm_model:
            for name in _TASK_MODEL_FIELDS:
                if name not in self.model_fields_set:
                    setattr(self, name, self.llm_model)
        return self

    @property
    def points_by_rank(self) -> tuple[int, int, int]:
        """Points for ranks 1, 2 and 3."""
        return (self.points_first, self.points_second, self.points_third)
