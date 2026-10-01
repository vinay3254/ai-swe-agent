import pytest
from pydantic import ValidationError

from swe_agent.config import Thresholds, load_settings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-key")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "jev-test")
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", '["octo/app"]')
    monkeypatch.setenv("SWE_AGENT_THRESHOLDS__MAX_COMPLEXITY", "3.5")

    settings = load_settings()

    assert settings.typesafe_api_key.get_secret_value() == "secret-key"
    assert settings.jev_model == "jev-test"
    assert settings.repo_allowlist == ["octo/app"]
    assert settings.thresholds.max_complexity == 3.5
    assert "secret-key" not in repr(settings)


def test_settings_require_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.delenv("SWE_AGENT_JEV_MODEL", raising=False)

    with pytest.raises(ValidationError):
        load_settings()


def test_thresholds_reject_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        Thresholds.model_validate({"triage_min_confidence": 1.5})


def test_runtime_settings_have_safe_defaults_and_read_standard_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_x")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.setenv("SWE_AGENT_API_TOKEN", "svc")
    monkeypatch.delenv("SWE_AGENT_TEST_COMMAND", raising=False)

    s = load_settings()

    assert s.github_token is not None and s.github_token.get_secret_value() == "ghp_x"
    assert s.anthropic_api_key is not None and s.anthropic_api_key.get_secret_value() == "sk-ant"
    assert s.api_token is not None and s.api_token.get_secret_value() == "svc"
    assert s.agent_model == "anthropic/claude-sonnet-5-5"
    assert s.test_command is None
    assert s.max_iterations == 100
    assert s.max_concurrent_jobs == 2
    assert s.repo_allowlist == []
    assert "ghp_x" not in repr(s) and "sk-ant" not in repr(s)


def test_llm_base_url_and_key_can_point_at_an_openai_compatible_gateway(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("SWE_AGENT_LLM_BASE_URL", "http://localhost:20128/v1")
    monkeypatch.setenv("SWE_AGENT_LLM_API_KEY", "omni-key")

    s = load_settings()

    assert s.llm_base_url == "http://localhost:20128/v1"
    assert s.effective_llm_api_key() == "omni-key"
    assert "omni-key" not in repr(s)


def test_effective_llm_key_prefers_the_gateway_key_then_falls_back_to_anthropic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant")
    monkeypatch.delenv("SWE_AGENT_LLM_API_KEY", raising=False)
    assert load_settings().effective_llm_api_key() == "sk-ant"

    monkeypatch.setenv("SWE_AGENT_LLM_API_KEY", "gw")
    assert load_settings().effective_llm_api_key() == "gw"

    monkeypatch.delenv("SWE_AGENT_LLM_API_KEY")
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert load_settings().effective_llm_api_key() is None
