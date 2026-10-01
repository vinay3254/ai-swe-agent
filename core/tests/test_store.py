from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from swe_agent.models import JobState
from swe_agent.store import Store


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


def test_begin_job_creates_received_job(store: Store) -> None:
    job, created = store.begin_job("k1", "https://github.com/o/r/issues/1")

    assert created is True
    assert job.state is JobState.RECEIVED
    assert job.pr_url is None


def test_begin_job_with_same_key_is_idempotent(store: Store) -> None:
    store.begin_job("k1", "https://github.com/o/r/issues/1")
    store.set_state("k1", JobState.RUNNING)

    job, created = store.begin_job("k1", "https://github.com/o/r/issues/1")

    assert created is False
    assert job.state is JobState.RUNNING


def test_set_state_keeps_earlier_pr_url_and_reason(store: Store) -> None:
    store.begin_job("k1", "u")
    store.set_state("k1", JobState.DONE, pr_url="https://github.com/o/r/pull/9", reason="ok")
    store.set_state("k1", JobState.DONE)

    job = store.get_job("k1")
    assert job is not None
    assert job.pr_url == "https://github.com/o/r/pull/9"
    assert job.reason == "ok"


def test_set_state_on_unknown_job_raises(store: Store) -> None:
    with pytest.raises(KeyError):
        store.set_state("missing", JobState.FAILED)


def test_get_job_returns_none_for_unknown_key(store: Store) -> None:
    assert store.get_job("missing") is None


def test_decisions_round_trip_in_insertion_order(store: Store) -> None:
    store.begin_job("k1", "u")
    store.log_decision("k1", "kind", "bug", 0.85)
    store.log_decision("k1", "fixable", True, 0.8)

    records = store.decisions("k1")

    assert [(r.question, r.value, r.confidence) for r in records] == [
        ("kind", "bug", 0.85),
        ("fixable", True, 0.8),
    ]
    assert store.decisions("other") == []


def test_store_is_usable_from_many_threads_and_creates_each_job_once(tmp_path: Path) -> None:
    store = Store(tmp_path / "jobs.db")

    def begin(_: int) -> bool:
        return store.begin_job("same-key", "u")[1]

    with ThreadPoolExecutor(max_workers=8) as pool:
        created = list(pool.map(begin, range(32)))

    assert created.count(True) == 1


def test_unfinished_jobs_lists_only_non_terminal_states(store: Store) -> None:
    for key, state in [
        ("a", JobState.RECEIVED),
        ("b", JobState.RUNNING),
        ("c", JobState.DONE),
        ("d", JobState.ESCALATED),
        ("e", JobState.FAILED),
    ]:
        store.begin_job(key, f"https://github.com/o/r/issues/{key}")
        store.set_state(key, state)

    assert sorted(j.key for j in store.unfinished_jobs()) == ["a", "b"]
