from collections.abc import Callable
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field

from swe_agent.jev_gate.client import JevUnavailable
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import IssueKind, Review, Triage
from swe_agent.models import Issue, IssueRef
from swe_agent.pipeline import RunRequest, RunResult


def good_triage() -> Triage:
    return Triage(
        kind=Decision(value=IssueKind.BUG, confidence=0.9, probabilities={}),
        complexity=Decision(value=1.0, confidence=0.9, probabilities={}),
        fixable=Decision(value=True, confidence=0.9, probabilities={}),
    )


def good_review() -> Review:
    return Review(
        addresses_issue=Decision(value=True, confidence=0.9, probabilities={}),
        risk=Decision(value=0.2, confidence=0.9, probabilities={}),
    )


def good_result() -> RunResult:
    return RunResult(
        branch="ai-fix/issue-1",
        diff="--- a/x.py\n+++ b/x.py\n@@\n-bug\n+fix\n",
        tests_passed=True,
        incomplete=False,
        transcript="",
    )


@dataclass
class FakeGate:
    triage_result: Triage | Exception = field(default_factory=good_triage)
    review_result: Review | Exception = field(default_factory=good_review)
    review_calls: int = 0

    def triage(self, issue: Issue) -> Triage:
        if isinstance(self.triage_result, Exception):
            raise self.triage_result
        return self.triage_result

    def review(self, issue: Issue, diff: str) -> Review:
        self.review_calls += 1
        if isinstance(self.review_result, Exception):
            raise self.review_result
        return self.review_result


@dataclass
class FakeRunner:
    result: RunResult | Exception = field(default_factory=good_result)
    calls: list[RunRequest] = field(default_factory=list)

    def run(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class PullRequest:
    branch: str
    title: str
    body: str
    draft: bool
    labels: list[str]


@dataclass
class FakeGitHub:
    comment_error: Exception | None = None
    comments: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    prs: list[PullRequest] = field(default_factory=list)

    def fetch_issue(self, ref: IssueRef) -> Issue:
        return Issue(ref=ref, title="Crash on empty input", body="parse('') raises", labels=())

    def comment(self, ref: IssueRef, body: str) -> None:
        if self.comment_error is not None:
            raise self.comment_error
        self.comments.append(body)

    def add_label(self, ref: IssueRef, label: str) -> None:
        self.labels.append(label)

    def open_pr(
        self, ref: IssueRef, *, branch: str, title: str, body: str, draft: bool, labels: list[str]
    ) -> str:
        self.prs.append(PullRequest(branch, title, body, draft, labels))
        return f"https://github.com/{ref.full_name}/pull/{100 + len(self.prs)}"


def jev_down() -> JevUnavailable:
    return JevUnavailable("Jev unavailable after 4 attempts: HTTP 529")


class InlineExecutor(Executor):
    """Runs submitted work immediately on the calling thread, so tests see final state."""

    def submit[**P, R](self, fn: Callable[P, R], /, *args: P.args, **kwargs: P.kwargs) -> "Future[R]":
        future: Future[R] = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as exc:  # noqa: BLE001
            future.set_exception(exc)
        return future
