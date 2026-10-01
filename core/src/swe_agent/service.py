from collections.abc import Sequence
from concurrent.futures import Executor

from swe_agent.models import IssueRef
from swe_agent.pipeline import Pipeline
from swe_agent.store import Job, Store


class RepoNotAllowed(Exception):
    pass


class JobConflict(Exception):
    """The job key exists but belongs to a different issue."""


def ensure_allowed(ref: IssueRef, allowlist: Sequence[str]) -> None:
    """Raise RepoNotAllowed unless `ref`'s repo is listed. An empty list denies everything."""
    if ref.full_name.lower() not in {name.lower() for name in allowlist}:
        raise RepoNotAllowed(f"{ref.full_name} is not in the repo allowlist")


class JobService:
    """Accepts jobs for allowlisted repos and runs them in the background."""

    def __init__(
        self,
        *,
        pipeline: Pipeline,
        store: Store,
        allowlist: Sequence[str],
        executor: Executor,
    ) -> None:
        self._pipeline = pipeline
        self._store = store
        self._allowlist = list(allowlist)
        self._executor = executor

    def start(self) -> list[Job]:
        """Fail jobs that a previous process left unfinished. Call before taking requests."""
        return self._pipeline.recover_interrupted()

    def submit(self, ref: IssueRef, job_key: str) -> Job:
        ensure_allowed(ref, self._allowlist)
        job, created = self._store.begin_job(job_key, ref.url)
        if not created:
            if job.issue_url != ref.url:
                raise JobConflict(f"job key {job_key!r} already belongs to {job.issue_url}")
            return job
        self._executor.submit(self._pipeline.run_claimed, ref, job_key)
        return job

    def get(self, job_key: str) -> Job | None:
        return self._store.get_job(job_key)
