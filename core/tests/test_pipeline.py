import httpx
import pytest
import respx
from fakes import FakeGate, FakeGitHub, FakeRunner, good_result, good_triage, jev_down

from swe_agent.config import Thresholds
from swe_agent.jev_gate.client import JevAuthError
from swe_agent.jev_gate.client import JevClient
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import IssueKind, JevGate, Triage
from swe_agent.models import IssueRef, JobState
from swe_agent.pipeline import Pipeline
from swe_agent.store import Store

REF = IssueRef(owner="octo", repo="app", number=1)


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def gate() -> FakeGate:
    return FakeGate()


@pytest.fixture
def pipeline(store: Store, github: FakeGitHub, runner: FakeRunner, gate: FakeGate) -> Pipeline:
    return Pipeline(
        store=store, github=github, runner=runner, gate=gate, thresholds=Thresholds()
    )


def test_happy_path_opens_a_ready_pr(
    pipeline: Pipeline, store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.DONE
    assert job.pr_url == "https://github.com/octo/app/pull/101"
    assert len(github.prs) == 1
    pr = github.prs[0]
    assert (pr.draft, pr.labels, pr.branch) == (False, [], "ai-fix/issue-1")
    assert "Resolves #1" in pr.body
    assert github.labels == []
    assert runner.calls[0].branch == "ai-fix/issue-1"
    assert [d.question for d in store.decisions("job-1")] == [
        "kind",
        "complexity",
        "fixable",
        "addresses_issue",
        "risk",
    ]


def test_low_confidence_triage_escalates_without_running_the_agent(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    unsure = Triage(
        kind=Decision(value=IssueKind.BUG, confidence=0.3, probabilities={}),
        complexity=good_triage().complexity,
        fixable=good_triage().fixable,
    )
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=unsure),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert runner.calls == []
    assert github.labels == ["needs-human"]
    assert "unsure" in github.comments[0]
    assert github.prs == []


def test_jev_down_at_triage_escalates_instead_of_proceeding(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=jev_down()),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert job.reason is not None and "Jev unavailable" in job.reason
    assert runner.calls == [] and github.prs == []
    assert github.labels == ["needs-human"]


def test_jev_auth_error_at_review_escalates_and_opens_no_pr(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(review_result=JevAuthError("HTTP 401")),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert github.prs == []


def test_runner_crash_marks_job_failed_and_comments(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=RuntimeError("sandbox died")),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.FAILED
    assert job.reason == "RuntimeError: sandbox died"
    assert "sandbox died" in github.comments[0]
    assert github.labels == []


def test_empty_diff_escalates_and_skips_the_review_gate(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    empty = good_result().model_copy(update={"diff": "  \n"})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=empty),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert gate.review_calls == 0
    assert github.prs == []
    assert "no changes" in github.comments[0]


def test_replayed_job_key_does_nothing_the_second_time(
    pipeline: Pipeline, github: FakeGitHub, runner: FakeRunner
) -> None:
    first = pipeline.run(REF, "job-1")
    second = pipeline.run(REF, "job-1")

    assert second == first
    assert len(runner.calls) == 1
    assert len(github.prs) == 1


def test_incomplete_run_opens_a_labeled_draft_pr(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    partial = good_result().model_copy(update={"incomplete": True})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=partial),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.DONE
    assert github.prs[0].draft is True
    assert github.prs[0].labels == ["incomplete"]


def test_failing_tests_open_a_draft_pr(store: Store, github: FakeGitHub, gate: FakeGate) -> None:
    failing = good_result().model_copy(update={"tests_passed": False})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=failing),
        gate=gate,
        thresholds=Thresholds(),
    )

    pipeline.run(REF, "job-1")

    assert github.prs[0].draft is True
    assert "Tests: failed" in github.prs[0].body


def test_github_comment_failure_does_not_hide_the_escalation(
    store: Store, runner: FakeRunner
) -> None:
    github = FakeGitHub(comment_error=RuntimeError("GitHub 502"))
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=jev_down()),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert job.reason is not None and "Jev unavailable" in job.reason


@respx.mock
def test_out_of_range_jev_answer_escalates_with_needs_human(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    answer = {"probabilities": {}, "confidence": 0.9}
    body = {
        "model": "m",
        "answers": {
            "kind": {"type": "choice", "choice": "bug", **answer},
            "complexity": {"type": "score", "score": 1.0, "legend": {}, **answer},
            "fixable": {"type": "noul", "noul": 1.5},
        },
    }
    respx.post("https://api.jev.test/v1/systemone").mock(
        return_value=httpx.Response(200, json=body)
    )
    client = JevClient(
        api_key="k",
        model="m",
        base_url="https://api.jev.test",
        timeout_s=5,
        max_attempts=1,
        sleep=lambda _: None,
    )
    gate = JevGate(client, max_issue_chars=1000, max_diff_chars=1000)
    pipeline = Pipeline(
        store=store, github=github, runner=runner, gate=gate, thresholds=Thresholds()
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert job.reason is not None and "malformed" in job.reason
    assert github.labels == ["needs-human"]
    assert runner.calls == [] and github.prs == []
