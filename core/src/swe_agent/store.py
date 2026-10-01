import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

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


class Store:
    """SQLite persistence for jobs and the Jev decision log."""

    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

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

    def get_job(self, key: str) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
        return None if row is None else Job.model_validate(dict(row))

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

    def log_decision(self, key: str, question: str, value: object, confidence: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO decisions (job_key, question, value, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, question, json.dumps(value), confidence, _now()),
            )

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
