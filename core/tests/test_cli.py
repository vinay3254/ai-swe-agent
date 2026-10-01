import pytest
from fakes import FakeGate, FakeGitHub, FakeRunner, jev_down
from typer.testing import CliRunner

import swe_agent.cli as cli
from swe_agent.config import Thresholds
from swe_agent.pipeline import Pipeline
from swe_agent.store import Store

URL = "https://github.com/octo/app/issues/1"
runner_cli = CliRunner()


def pipeline_with(gate: FakeGate) -> Pipeline:
    return Pipeline(
        store=Store(":memory:"),
        github=FakeGitHub(),
        runner=FakeRunner(),
        gate=gate,
        thresholds=Thresholds(),
    )


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", '["octo/app"]')


def test_fix_prints_pr_url_and_exits_zero_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "build_pipeline", lambda s: (pipeline_with(FakeGate()), Store(":memory:")))

    result = runner_cli.invoke(cli.app, ["fix", URL])

    assert result.exit_code == 0, result.output
    assert "done" in result.output
    assert "https://github.com/octo/app/pull/101" in result.output


def test_fix_exits_two_when_escalated(monkeypatch: pytest.MonkeyPatch) -> None:
    pipeline = pipeline_with(FakeGate(triage_result=jev_down()))
    monkeypatch.setattr(cli, "build_pipeline", lambda s: (pipeline, Store(":memory:")))

    result = runner_cli.invoke(cli.app, ["fix", URL])

    assert result.exit_code == 2
    assert "escalated" in result.output and "Jev unavailable" in result.output


def test_fix_rejects_repos_outside_the_allowlist_without_building_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def boom(_: object) -> None:
        raise AssertionError("must not build a pipeline for a forbidden repo")

    monkeypatch.setattr(cli, "build_pipeline", boom)

    result = runner_cli.invoke(cli.app, ["fix", "https://github.com/evil/repo/issues/1"])

    assert result.exit_code == 1
    assert "allowlist" in result.output


def test_fix_rejects_a_non_issue_url(monkeypatch: pytest.MonkeyPatch) -> None:
    result = runner_cli.invoke(cli.app, ["fix", "https://github.com/octo/app/pull/1"])

    assert result.exit_code != 0
    assert "not a GitHub issue URL" in result.output


def test_openapi_command_prints_the_schema() -> None:
    result = runner_cli.invoke(cli.app, ["openapi"])

    assert result.exit_code == 0
    assert '"/jobs"' in result.output
