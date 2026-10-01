import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from swe_agent.config import Thresholds
from swe_agent.jev_gate.client import JevError
from swe_agent.jev_gate.questions import Gate
from swe_agent.models import Issue, IssueRef, JobState
from swe_agent.outcome import Outcome, Verdict, decide_outcome, triage_blocker
from swe_agent.store import Job, Store

log = logging.getLogger(__name__)

NEEDS_HUMAN = "needs-human"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RunRequest:
    issue: Issue
    branch: str


class RunResult(BaseModel, frozen=True):
    branch: str
    diff: str
    tests_passed: bool
    incomplete: bool  # True when the agent hit its step or time budget
    transcript: str


class GitHubPort(Protocol):
    def fetch_issue(self, ref: IssueRef) -> Issue: ...
    def comment(self, ref: IssueRef, body: str) -> None: ...
    def add_label(self, ref: IssueRef, label: str) -> None: ...
    def open_pr(
        self, ref: IssueRef, *, branch: str, title: str, body: str, draft: bool, labels: list[str]
    ) -> str:
        """Return the PR URL."""
        ...


class RunnerPort(Protocol):
    def run(self, request: RunRequest) -> RunResult: ...


class Pipeline:
    """State machine for one issue job: triage, run, review, then PR or escalation."""

    def __init__(
        self,
        *,
        store: Store,
        github: GitHubPort,
        runner: RunnerPort,
        gate: Gate,
        thresholds: Thresholds,
    ) -> None:
        self._store = store
        self._github = github
        self._runner = runner
        self._gate = gate
        self._t = thresholds

    def run(self, ref: IssueRef, job_key: str) -> Job:
        """Run one job. A repeated `job_key` returns the existing job and does nothing."""
        job, created = self._store.begin_job(job_key, ref.url)
        if not created:
            return job
        try:
            self._execute(ref, job_key)
        except JevError as exc:
            self._finish(ref, job_key, JobState.ESCALATED, f"Jev unavailable: {exc}", NEEDS_HUMAN)
        except Exception as exc:  # noqa: BLE001 - any crash must end in a visible failed job
            log.exception("job %s crashed", job_key)
            self._finish(ref, job_key, JobState.FAILED, f"{type(exc).__name__}: {exc}", None)
        final = self._store.get_job(job_key)
        assert final is not None
        return final

    def _execute(self, ref: IssueRef, key: str) -> None:
        issue = self._github.fetch_issue(ref)

        triage = self._gate.triage(issue)
        self._store.log_decision(key, "kind", triage.kind.value.value, triage.kind.confidence)
        self._store.log_decision(
            key, "complexity", triage.complexity.value, triage.complexity.confidence
        )
        self._store.log_decision(key, "fixable", triage.fixable.value, triage.fixable.confidence)
        blocker = triage_blocker(triage, self._t)
        if blocker is not None:
            self._finish(ref, key, JobState.ESCALATED, blocker, NEEDS_HUMAN)
            return
        self._store.set_state(key, JobState.TRIAGED)

        self._store.set_state(key, JobState.RUNNING)
        result = self._runner.run(RunRequest(issue=issue, branch=f"ai-fix/issue-{ref.number}"))
        if not result.diff.strip():
            self._finish(
                ref, key, JobState.ESCALATED, "the agent produced no changes", NEEDS_HUMAN
            )
            return

        review = self._gate.review(issue, result.diff)
        self._store.log_decision(
            key, "addresses_issue", review.addresses_issue.value, review.addresses_issue.confidence
        )
        self._store.log_decision(key, "risk", review.risk.value, review.risk.confidence)
        self._store.set_state(key, JobState.REVIEWED)

        verdict = decide_outcome(
            review=review,
            tests_passed=result.tests_passed,
            incomplete=result.incomplete,
            thresholds=self._t,
        )
        if verdict.outcome is Outcome.ESCALATE:
            self._finish(ref, key, JobState.ESCALATED, verdict.reason, NEEDS_HUMAN)
            return

        pr_url = self._github.open_pr(
            ref,
            branch=result.branch,
            title=f"Fix #{ref.number}: {issue.title}",
            body=_pr_body(ref, result, verdict),
            draft=verdict.outcome is Outcome.DRAFT_PR,
            labels=[INCOMPLETE] if result.incomplete else [],
        )
        self._store.set_state(key, JobState.DONE, pr_url=pr_url, reason=verdict.reason)

    def _finish(
        self, ref: IssueRef, key: str, state: JobState, reason: str, label: str | None
    ) -> None:
        """Record the end state first, then tell the issue. GitHub errors must not undo the record."""
        self._store.set_state(key, state, reason=reason)
        try:
            self._github.comment(ref, _issue_comment(state, reason))
            if label is not None:
                self._github.add_label(ref, label)
        except Exception:  # noqa: BLE001
            log.exception("could not report job %s on %s", key, ref.url)


def _issue_comment(state: JobState, reason: str) -> str:
    if state is JobState.FAILED:
        return f"The agent failed on this issue and made no PR.\n\nReason: {reason}"
    return f"The agent is handing this issue to a human.\n\nReason: {reason}"


def _pr_body(ref: IssueRef, result: RunResult, verdict: Verdict) -> str:
    tests = "passed" if result.tests_passed else "failed"
    return f"Resolves #{ref.number}.\n\nTests: {tests}.\n\nGate: {verdict.reason}."
