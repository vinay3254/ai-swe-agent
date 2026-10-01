import pytest
from fakes import FakeGate, FakeGitHub, FakeRunner, InlineExecutor

from swe_agent.config import Thresholds
from swe_agent.models import IssueRef, JobState
from swe_agent.pipeline import Pipeline
from swe_agent.service import JobConflict, JobService, RepoNotAllowed
from swe_agent.store import Store

REF = IssueRef(owner="Octo", repo="App", number=1)


def make(allowlist: list[str]) -> tuple[JobService, FakeGitHub, FakeRunner, Store]:
    store, github, runner = Store(":memory:"), FakeGitHub(), FakeRunner()
    pipeline = Pipeline(
        store=store, github=github, runner=runner, gate=FakeGate(), thresholds=Thresholds()
    )
    service = JobService(
        pipeline=pipeline, store=store, allowlist=allowlist, executor=InlineExecutor()
    )
    return service, github, runner, store


def test_submit_runs_the_job_for_an_allowlisted_repo_ignoring_case() -> None:
    service, github, _, store = make(["octo/app"])

    job = service.submit(REF, "k1")

    assert job.key == "k1"
    final = store.get_job("k1")
    assert final is not None and final.state is JobState.DONE
    assert len(github.prs) == 1


def test_empty_allowlist_denies_everything() -> None:
    service, github, runner, store = make([])

    with pytest.raises(RepoNotAllowed):
        service.submit(REF, "k1")

    assert store.get_job("k1") is None and runner.calls == [] and github.prs == []


def test_unlisted_repo_is_rejected_before_any_work() -> None:
    service, _, runner, store = make(["someone/else"])

    with pytest.raises(RepoNotAllowed, match="Octo/App"):
        service.submit(REF, "k1")

    assert store.get_job("k1") is None and runner.calls == []


def test_same_key_twice_runs_once() -> None:
    service, github, runner, _ = make(["octo/app"])

    first = service.submit(REF, "k1")
    second = service.submit(REF, "k1")

    assert first.key == second.key
    assert len(runner.calls) == 1 and len(github.prs) == 1


def test_same_key_for_a_different_issue_is_a_conflict() -> None:
    service, _, runner, _ = make(["octo/app"])
    service.submit(REF, "k1")

    with pytest.raises(JobConflict):
        service.submit(IssueRef(owner="Octo", repo="App", number=2), "k1")

    assert len(runner.calls) == 1


def test_start_recovers_interrupted_jobs() -> None:
    service, github, _, store = make(["octo/app"])
    store.begin_job("old", REF.url)
    store.set_state("old", JobState.RUNNING)

    recovered = service.start()

    assert [j.key for j in recovered] == ["old"]
    job = store.get_job("old")
    assert job is not None and job.state is JobState.FAILED
