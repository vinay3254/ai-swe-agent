import shutil
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from swe_agent.gitrepo import GitRepo
from swe_agent.models import Issue, IssueRef
from swe_agent.pipeline import RunRequest, RunResult


@dataclass(frozen=True)
class AgentRun:
    completed: bool  # False when the agent hit its step or cost budget, got stuck, or errored
    tests_passed: bool
    transcript: str


class AgentSession(Protocol):
    """Runs a coding agent inside a sandbox on `workdir`, then runs the repo's tests there."""

    def run(self, workdir: Path, prompt: str) -> AgentRun: ...


def build_prompt(issue: Issue) -> str:
    return (
        f"Resolve this GitHub issue in the repository at the current directory.\n\n"
        f"Title: {issue.title}\n\n{issue.body}\n\n"
        "Find the cause, make the smallest correct change, and add or update tests. "
        "Run the tests before you finish. "
        "Do not commit, push, or change git remotes; a separate step publishes your work."
    )


class GitRunner:
    """`RunnerPort`: clone, branch, let the agent work, collect the diff, push the branch."""

    def __init__(
        self,
        *,
        session: AgentSession,
        clone_url: Callable[[IssueRef], str],
        token: str | None,
        workspace_root: Path,
    ) -> None:
        self._session = session
        self._clone_url = clone_url
        self._token = token
        self._root = workspace_root

    def run(self, request: RunRequest) -> RunResult:
        self._root.mkdir(parents=True, exist_ok=True)
        workdir = Path(tempfile.mkdtemp(prefix="job-", dir=self._root)) / "repo"
        try:
            repo = GitRepo.clone(self._clone_url(request.issue.ref), workdir, token=self._token)
            base = repo.head_sha()
            repo.create_branch(request.branch)
            agent = self._session.run(workdir, build_prompt(request.issue))
            diff = repo.diff_since(base)
            if diff:
                repo.commit_all(f"Fix #{request.issue.ref.number}: {request.issue.title}")
                repo.push(request.branch)
            return RunResult(
                branch=request.branch,
                diff=diff,
                tests_passed=agent.tests_passed and bool(diff),
                incomplete=not agent.completed,
                transcript=agent.transcript,
            )
        finally:
            shutil.rmtree(workdir.parent, ignore_errors=True)
