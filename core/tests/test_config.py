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
