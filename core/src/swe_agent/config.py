from pathlib import Path
from typing import Any

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
    # Watchdog: a wrong notice harms a third party, so the bar for a violation is higher.
    watchdog_min_confidence: float = Field(default=0.85, ge=0.0, le=1.0)


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
    # Credentials below are optional so the CLI can run partial commands. Wiring checks them.
    github_token: SecretStr | None = Field(default=None, validation_alias="GITHUB_TOKEN")
    anthropic_api_key: SecretStr | None = Field(default=None, validation_alias="ANTHROPIC_API_KEY")
    # Point the agent's LLM at an OpenAI-compatible gateway such as OmniRoute
    # (http://localhost:20128/v1) with agent_model="openai/<model>". The key overrides ANTHROPIC_API_KEY.
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    api_token: SecretStr | None = None  # bearer token the gateway sends to the HTTP API
    agent_model: str = "anthropic/claude-sonnet-5-5"  # LiteLLM model id
    test_command: str | None = None  # None: detect from the repo's files
    max_iterations: int = Field(default=100, ge=1)
    max_concurrent_jobs: int = Field(default=2, ge=1)
    server_image: str = "ghcr.io/openhands/agent-server:latest-python"
    workspace_root: Path = Path(".agent-work")
    # MCP servers handed to the agent, keyed by name, in OpenHands MCPServer form.
    # Watchdog email digest. Without smtp_host the digest prints to stdout.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None
    digest_to: str | None = None
    mcp_servers: dict[str, dict[str, Any]] = Field(default_factory=dict)
    repo_allowlist: list[str] = Field(default_factory=list)
    db_path: Path = Path("swe_agent.db")
    thresholds: Thresholds = Field(default_factory=Thresholds)

    def effective_llm_api_key(self) -> str | None:
        """The gateway key if set, else the Anthropic key."""
        key = self.llm_api_key or self.anthropic_api_key
        return None if key is None else key.get_secret_value()


def load_settings() -> Settings:
    """Build Settings from the environment. Pyright cannot see env-sourced fields."""
    return Settings()  # pyright: ignore[reportCallIssue]
