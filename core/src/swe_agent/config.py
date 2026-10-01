from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Thresholds(BaseModel, frozen=True):
    """Every Jev-driven cutoff in one place. Tune these from the decision log."""

    triage_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Complexity is a Jev score on a 0-4 scale (trivial .. epic).
    max_complexity: float = Field(default=2.0, ge=0.0, le=4.0)
    review_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Risk is a Jev score on a 0-2 scale (low .. high). Above this, the PR is a draft.
    max_ready_risk: float = Field(default=0.5, ge=0.0, le=2.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWE_AGENT_", env_nested_delimiter="__")

    # The SDK documents TYPESAFE_API_KEY, so this field ignores the SWE_AGENT_ prefix.
    typesafe_api_key: SecretStr = Field(validation_alias="TYPESAFE_API_KEY")
    # Model name comes from console.typesafe.ai. No default on purpose.
    jev_model: str
    jev_base_url: str = "https://api.typesafe.ai"
    jev_timeout_s: float = Field(default=30.0, gt=0)
    jev_max_attempts: int = Field(default=4, ge=1)
    max_issue_chars: int = Field(default=20_000, gt=0)
    max_diff_chars: int = Field(default=60_000, gt=0)
    repo_allowlist: list[str] = Field(default_factory=list)
    db_path: Path = Path("swe_agent.db")
    thresholds: Thresholds = Field(default_factory=Thresholds)


def load_settings() -> Settings:
    """Build Settings from the environment. Pyright cannot see env-sourced fields."""
    return Settings()  # pyright: ignore[reportCallIssue]
