from pathlib import Path

import pytest

from swe_agent.config import Settings, load_settings
from swe_agent.service import JobService
from swe_agent.wiring import ConfigError, build_pipeline, build_service


def settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, **env: str) -> Settings:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.setenv("SWE_AGENT_DB_PATH", str(tmp_path / "db.sqlite"))
    monkeypatch.setenv("SWE_AGENT_WORKSPACE_ROOT", str(tmp_path / "ws"))
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", '["octo/app"]')
    for name in ("GITHUB_TOKEN", "ANTHROPIC_API_KEY", "SWE_AGENT_API_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    return load_settings()


def test_missing_credentials_are_reported_together(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="GITHUB_TOKEN.*ANTHROPIC_API_KEY"):
        build_pipeline(settings(monkeypatch, tmp_path))


def test_complete_settings_build_a_pipeline_and_service(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = settings(monkeypatch, tmp_path, GITHUB_TOKEN="ghp", ANTHROPIC_API_KEY="sk")

    pipeline, store = build_pipeline(s)
    service = build_service(s)

    assert pipeline is not None and store is not None
    assert isinstance(service, JobService)
    assert (tmp_path / "db.sqlite").exists()


def test_gateway_key_alone_is_enough_without_an_anthropic_key(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    s = settings(
        monkeypatch,
        tmp_path,
        GITHUB_TOKEN="ghp",
        SWE_AGENT_LLM_API_KEY="omni",
        SWE_AGENT_LLM_BASE_URL="http://localhost:20128/v1",
        SWE_AGENT_AGENT_MODEL="openai/auto",
    )

    pipeline, _ = build_pipeline(s)

    assert pipeline is not None


def test_missing_llm_key_error_names_both_ways_to_set_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    with pytest.raises(ConfigError, match="ANTHROPIC_API_KEY or SWE_AGENT_LLM_API_KEY"):
        build_pipeline(settings(monkeypatch, tmp_path, GITHUB_TOKEN="ghp"))
