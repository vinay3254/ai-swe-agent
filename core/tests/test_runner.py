from dataclasses import dataclass, field
from pathlib import Path

import pytest
from gitfixtures import git, make_remote

from swe_agent.models import Issue, IssueRef
from swe_agent.pipeline import RunRequest
from swe_agent.runner import AgentRun, GitRunner, build_prompt

REF = IssueRef(owner="octo", repo="app", number=1)
ISSUE = Issue(ref=REF, title="Crash on empty input", body="parse('') raises IndexError", labels=())


@dataclass
class FakeSession:
    edit: str | None = "def parse(s):\n    return s[:1]\n"
    completed: bool = True
    tests_passed: bool = True
    commit_itself: bool = False
    error: Exception | None = None
    prompts: list[str] = field(default_factory=list)
    workdirs: list[Path] = field(default_factory=list)

    def run(self, workdir: Path, prompt: str) -> AgentRun:
        self.prompts.append(prompt)
        self.workdirs.append(workdir)
        if self.error is not None:
            raise self.error
        if self.edit is not None:
            (workdir / "app.py").write_text(self.edit)
            if self.commit_itself:
                git("add", "-A", cwd=workdir)
                git("commit", "-m", "agent commit", cwd=workdir)
        return AgentRun(completed=self.completed, tests_passed=self.tests_passed, transcript="log")


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    return make_remote(tmp_path)


def make_runner(tmp_path: Path, remote: Path, session: FakeSession) -> GitRunner:
    return GitRunner(
        session=session,
        clone_url=lambda ref: str(remote),
        token=None,
        workspace_root=tmp_path / "ws",
    )


def request() -> RunRequest:
    return RunRequest(issue=ISSUE, branch="ai-fix/issue-1")


def test_successful_run_returns_diff_and_pushes_the_branch(tmp_path: Path, remote: Path) -> None:
    session = FakeSession()

    result = make_runner(tmp_path, remote, session).run(request())

    assert "+    return s[:1]" in result.diff
    assert (result.tests_passed, result.incomplete, result.branch) == (True, False, "ai-fix/issue-1")
    assert result.transcript == "log"
    assert "Fix #1" in git("log", "-1", "--format=%s", "ai-fix/issue-1", cwd=remote)


def test_incomplete_agent_still_publishes_partial_work(tmp_path: Path, remote: Path) -> None:
    result = make_runner(tmp_path, remote, FakeSession(completed=False)).run(request())

    assert result.incomplete is True
    assert git("branch", "--list", "ai-fix/issue-1", cwd=remote) != ""


def test_no_changes_means_empty_diff_failed_tests_and_nothing_pushed(
    tmp_path: Path, remote: Path
) -> None:
    result = make_runner(tmp_path, remote, FakeSession(edit=None)).run(request())

    assert result.diff == ""
    assert result.tests_passed is False
    assert git("branch", "--list", "ai-fix/issue-1", cwd=remote) == ""


def test_changes_the_agent_committed_itself_are_still_pushed(tmp_path: Path, remote: Path) -> None:
    result = make_runner(tmp_path, remote, FakeSession(commit_itself=True)).run(request())

    assert "+    return s[:1]" in result.diff
    assert "agent commit" in git("log", "-1", "--format=%s", "ai-fix/issue-1", cwd=remote)


def test_failed_tests_are_reported_not_hidden(tmp_path: Path, remote: Path) -> None:
    result = make_runner(tmp_path, remote, FakeSession(tests_passed=False)).run(request())

    assert result.tests_passed is False


def test_work_directory_is_removed_after_success_and_after_a_crash(
    tmp_path: Path, remote: Path
) -> None:
    ok = FakeSession()
    make_runner(tmp_path, remote, ok).run(request())
    assert not ok.workdirs[0].exists()

    boom = FakeSession(error=RuntimeError("sandbox died"))
    with pytest.raises(RuntimeError, match="sandbox died"):
        make_runner(tmp_path, remote, boom).run(request())
    assert not boom.workdirs[0].exists()


def test_prompt_carries_the_issue_and_forbids_git_publishing() -> None:
    prompt = build_prompt(ISSUE)

    assert "Crash on empty input" in prompt
    assert "parse('') raises IndexError" in prompt
    assert "Do not commit, push, or change git remotes" in prompt
