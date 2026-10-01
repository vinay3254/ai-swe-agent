from pathlib import Path

import pytest
from typer.testing import CliRunner
from wd_fakes import CORE, ORIGINAL_FILES, FakeWatchGate, FakeWatchGitHub, fork

import swe_agent.cli as cli
import swe_agent.cli_watchdog as cli_wd
from swe_agent.config import Thresholds
from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.models import Verdict
from swe_agent.watchdog.scan import Watchdog
from swe_agent.wiring import WatchdogRuntime

runner = CliRunner()
STRIPPED = {"src/core.py": CORE, "README.md": "# mine\n"}


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch) -> WatchdogRuntime:
    gh = FakeWatchGitHub(
        repos={"jane/app": ORIGINAL_FILES, "mallory/app": STRIPPED}, forks=[fork("mallory/app")]
    )
    cases = CaseStore(":memory:")
    rt = WatchdogRuntime(
        watchdog=Watchdog(github=gh, gate=FakeWatchGate(), cases=cases, thresholds=Thresholds()),
        cases=cases,
        github=gh,  # pyright: ignore[reportArgumentType]
    )
    monkeypatch.setattr(cli_wd, "build_watchdog", lambda settings: rt)
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "m")
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", '["jane/app"]')
    monkeypatch.delenv("SWE_AGENT_SMTP_HOST", raising=False)
    return rt


def invoke(*args: str, input: str | None = None):  # noqa: ANN201
    return runner.invoke(cli.app, ["watchdog", *args], input=input)


def test_scan_reports_and_prints_the_digest(runtime: WatchdogRuntime) -> None:
    result = invoke("scan")

    assert result.exit_code == 0, result.output
    assert "jane/app" in result.output
    assert "VIOLATION #1" in result.output
    assert "agent watchdog notice 1" in result.output


def test_scan_without_any_repo_is_an_error(runtime: WatchdogRuntime, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", "[]")

    result = invoke("scan")

    assert result.exit_code == 1
    assert "no repos" in result.output


def test_cases_lists_open_cases(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    result = invoke("cases")

    assert "#1" in result.output and "violation" in result.output and "mallory/app" in result.output


def test_show_prints_evidence_and_the_draft_notice(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    result = invoke("show", "1")

    assert "https://github.com/mallory/app" in result.output
    assert "License attribution missing" in result.output


def test_notice_asks_for_approval_and_declining_sends_nothing(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    result = invoke("notice", "1", input="n\n")

    assert result.exit_code == 1
    assert runtime.github.issues_created == []  # pyright: ignore[reportAttributeAccessIssue]


def test_notice_sends_after_the_owner_approves(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    result = invoke("notice", "1", input="y\n")

    assert result.exit_code == 0, result.output
    assert "https://github.com/mallory/app/issues/1" in result.output
    assert len(runtime.github.issues_created) == 1  # pyright: ignore[reportAttributeAccessIssue]


def test_second_notice_for_the_same_case_is_refused_without_follow_up(runtime: WatchdogRuntime) -> None:
    invoke("scan")
    invoke("notice", "1", "--yes")

    result = invoke("notice", "1", "--yes")

    assert result.exit_code == 1 and "already" in result.output
    assert len(runtime.github.issues_created) == 1  # pyright: ignore[reportAttributeAccessIssue]

    assert invoke("notice", "1", "--yes", "--follow-up").exit_code == 0


def test_review_case_must_be_resolved_before_a_notice(runtime: WatchdogRuntime) -> None:
    case = runtime.cases.record("jane/app", "maybe/app", Verdict.REVIEW, "t", _evidence())

    blocked = invoke("notice", str(case.id), "--yes")
    assert blocked.exit_code == 1 and "review" in blocked.output

    assert invoke("resolve", str(case.id), "--violation").exit_code == 0
    assert invoke("notice", str(case.id), "--yes").exit_code == 0


def test_resolve_dismiss_closes_the_case_for_good(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    assert invoke("resolve", "1", "--dismiss").exit_code == 0
    result = invoke("notice", "1", "--yes")

    assert result.exit_code == 1 and "dismissed" in result.output


def test_resolve_needs_exactly_one_decision(runtime: WatchdogRuntime) -> None:
    invoke("scan")

    assert invoke("resolve", "1").exit_code != 0
    assert invoke("resolve", "1", "--violation", "--dismiss").exit_code != 0


def test_dmca_writes_a_draft_file_and_sends_nothing(runtime: WatchdogRuntime, tmp_path: Path) -> None:
    invoke("scan")
    out = tmp_path / "dmca.txt"

    result = invoke("dmca", "1", "--out", str(out))

    assert result.exit_code == 0
    assert out.read_text().startswith("DRAFT")
    assert runtime.github.issues_created == []  # pyright: ignore[reportAttributeAccessIssue]


def _evidence():  # noqa: ANN202
    from swe_agent.watchdog.models import Evidence, Source

    return Evidence(
        original_repo="jane/app",
        candidate_repo="maybe/app",
        candidate_url="https://github.com/maybe/app",
        source=Source.FORK,
        checked_at="2026-10-01T00:00:00+00:00",
        original_copyright=["Copyright (c) 2024 Jane Doe"],
        reason="unsure",
    )
