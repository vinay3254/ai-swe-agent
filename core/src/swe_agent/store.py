import functools
import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Concatenate

from pydantic import BaseModel

from swe_agent.models import JobState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key TEXT PRIMARY KEY,
    issue_url TEXT NOT NULL,
    state TEXT NOT NULL,
    pr_url TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL REFERENCES jobs(key),
    question TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Job(BaseModel, frozen=True):
    key: str
    issue_url: str
    state: JobState
    pr_url: str | None
    reason: str | None
    created_at: str
    updated_at: str


class DecisionRecord(BaseModel, frozen=True):
    job_key: str
    question: str
    value: object
    confidence: float
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _locked[**P, R](method: Callable[Concatenate["Store", P], R]) -> Callable[Concatenate["Store", P], R]:
    @functools.wraps(method)
    def wrapper(self: "Store", *args: P.args, **kwargs: P.kwargs) -> R:
        with self._lock:  # pyright: ignore[reportPrivateUsage]
            return method(self, *args, **kwargs)

    return wrapper


class Store:
    """SQLite persistence for jobs and the Jev decision log."""

    def __init__(self, path: Path | str) -> None:
        # One connection shared across threads (FastAPI runs sync handlers in a pool).
        # The lock serializes every statement; busy timeout covers other processes.
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        self._lock = threading.RLock()
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    @_locked
    def close(self) -> None:
        self._db.close()

    @_locked
    def begin_job(self, key: str, issue_url: str) -> tuple[Job, bool]:
        """Create the job if `key` is new. The bool is True only for the caller that created it."""
        now = _now()
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO jobs (key, issue_url, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, issue_url, JobState.RECEIVED.value, now, now),
            )
        job = self.get_job(key)
        assert job is not None
        return job, cursor.rowcount == 1

    @_locked
    def get_job(self, key: str) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
        return None if row is None else Job.model_validate(dict(row))

    @_locked
    def unfinished_jobs(self) -> list[Job]:
        terminal = (JobState.DONE.value, JobState.ESCALATED.value, JobState.FAILED.value)
        marks = ", ".join("?" for _ in terminal)
        rows = self._db.execute(
            f"SELECT * FROM jobs WHERE state NOT IN ({marks}) ORDER BY created_at", terminal
        ).fetchall()
        return [Job.model_validate(dict(r)) for r in rows]

    @_locked
    def set_state(
        self,
        key: str,
        state: JobState,
        *,
        pr_url: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self._db:
            cursor = self._db.execute(
                "UPDATE jobs SET state = ?, pr_url = COALESCE(?, pr_url), "
                "reason = COALESCE(?, reason), updated_at = ? WHERE key = ?",
                (state.value, pr_url, reason, _now(), key),
            )
        if cursor.rowcount == 0:
            raise KeyError(key)

    @_locked
    def log_decision(self, key: str, question: str, value: object, confidence: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO decisions (job_key, question, value, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, question, json.dumps(value), confidence, _now()),
            )

    @_locked
    def decisions(self, key: str) -> list[DecisionRecord]:
        rows = self._db.execute(
            "SELECT job_key, question, value, confidence, created_at "
            "FROM decisions WHERE job_key = ? ORDER BY id",
            (key,),
        ).fetchall()
        return [
            DecisionRecord(
                job_key=r["job_key"],
                question=r["question"],
                value=json.loads(r["value"]),
                confidence=r["confidence"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
