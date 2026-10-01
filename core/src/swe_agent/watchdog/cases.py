import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from swe_agent.watchdog.models import Case, CaseStatus, Evidence, Verdict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_repo TEXT NOT NULL,
    candidate_repo TEXT NOT NULL,
    verdict TEXT NOT NULL,
    status TEXT NOT NULL,
    pushed_at TEXT,
    evidence TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (original_repo, candidate_repo)
);
CREATE TABLE IF NOT EXISTS notices (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id INTEGER NOT NULL REFERENCES cases(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    approved_by TEXT NOT NULL,
    result_url TEXT,
    sent_at TEXT NOT NULL
);
"""


class Notice(BaseModel, frozen=True):
    id: int
    case_id: int
    kind: str
    title: str
    body: str
    approved_by: str
    result_url: str | None
    sent_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _status_for(verdict: Verdict) -> CaseStatus:
    return CaseStatus.CLOSED if verdict is Verdict.CLEAR else CaseStatus.OPEN


def _case(row: sqlite3.Row) -> Case:
    return Case(
        id=row["id"],
        original_repo=row["original_repo"],
        candidate_repo=row["candidate_repo"],
        verdict=Verdict(row["verdict"]),
        status=CaseStatus(row["status"]),
        pushed_at=row["pushed_at"],
        evidence=Evidence.model_validate_json(row["evidence"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class CaseStore:
    """SQLite store for watchdog cases and the audit trail of every notice sent."""

    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path), check_same_thread=False, timeout=30.0)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)
        self._lock = threading.RLock()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def record(
        self,
        original_repo: str,
        candidate_repo: str,
        verdict: Verdict,
        pushed_at: str | None,
        evidence: Evidence,
    ) -> Case:
        """Insert or refresh a case. Dismissed and notified cases are left exactly as they are."""
        now = _now()
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT * FROM cases WHERE original_repo = ? AND candidate_repo = ?",
                (original_repo, candidate_repo),
            ).fetchone()
            if row is None:
                cursor = self._db.execute(
                    "INSERT INTO cases (original_repo, candidate_repo, verdict, status, pushed_at, "
                    "evidence, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        original_repo,
                        candidate_repo,
                        verdict.value,
                        _status_for(verdict).value,
                        pushed_at,
                        evidence.model_dump_json(),
                        now,
                        now,
                    ),
                )
                case_id = int(cursor.lastrowid or 0)
            else:
                case_id = int(row["id"])
                if CaseStatus(row["status"]) in (CaseStatus.OPEN, CaseStatus.CLOSED):
                    self._db.execute(
                        "UPDATE cases SET verdict = ?, status = ?, pushed_at = ?, evidence = ?, "
                        "updated_at = ? WHERE id = ?",
                        (
                            verdict.value,
                            _status_for(verdict).value,
                            pushed_at,
                            evidence.model_dump_json(),
                            now,
                            case_id,
                        ),
                    )
        case = self.get(case_id)
        assert case is not None
        return case

    def seen_pushed_at(self, original_repo: str, candidate_repo: str) -> str | None:
        """Scan cursor: the candidate's `pushed_at` when we last assessed it."""
        with self._lock:
            row = self._db.execute(
                "SELECT pushed_at FROM cases WHERE original_repo = ? AND candidate_repo = ?",
                (original_repo, candidate_repo),
            ).fetchone()
        return None if row is None else row["pushed_at"]

    def get(self, case_id: int) -> Case | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM cases WHERE id = ?", (case_id,)).fetchone()
        return None if row is None else _case(row)

    def list_cases(self, *, status: CaseStatus | None = None, verdict: Verdict | None = None) -> list[Case]:
        clauses: list[str] = []
        params: list[str] = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status.value)
        if verdict is not None:
            clauses.append("verdict = ?")
            params.append(verdict.value)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._db.execute(f"SELECT * FROM cases {where} ORDER BY id", params).fetchall()
        return [_case(r) for r in rows]

    def dismiss(self, case_id: int) -> None:
        with self._lock, self._db:
            cursor = self._db.execute(
                "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
                (CaseStatus.DISMISSED.value, _now(), case_id),
            )
        if cursor.rowcount == 0:
            raise KeyError(case_id)

    def upgrade_to_violation(self, case_id: int) -> Case:
        """The owner reviewed a low-confidence case and decided it is a violation."""
        case = self.get(case_id)
        if case is None:
            raise KeyError(case_id)
        if case.status is not CaseStatus.OPEN:
            raise ValueError(f"case {case_id} is {case.status.value}, only an open case can be upgraded")
        evidence = case.evidence.model_copy(
            update={"notes": [*case.evidence.notes, f"{_now()}: owner confirmed this is a violation"]}
        )
        with self._lock, self._db:
            self._db.execute(
                "UPDATE cases SET verdict = ?, evidence = ?, updated_at = ? WHERE id = ?",
                (Verdict.VIOLATION.value, evidence.model_dump_json(), _now(), case_id),
            )
        upgraded = self.get(case_id)
        assert upgraded is not None
        return upgraded

    def record_notice(
        self,
        case_id: int,
        *,
        kind: str,
        title: str,
        body: str,
        approved_by: str,
        result_url: str | None,
    ) -> Notice:
        """Log a sent notice and mark its case notified, atomically."""
        now = _now()
        with self._lock, self._db:
            cursor = self._db.execute(
                "INSERT INTO notices (case_id, kind, title, body, approved_by, result_url, sent_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (case_id, kind, title, body, approved_by, result_url, now),
            )
            self._db.execute(
                "UPDATE cases SET status = ?, updated_at = ? WHERE id = ?",
                (CaseStatus.NOTIFIED.value, now, case_id),
            )
        return Notice(
            id=int(cursor.lastrowid or 0),
            case_id=case_id,
            kind=kind,
            title=title,
            body=body,
            approved_by=approved_by,
            result_url=result_url,
            sent_at=now,
        )

    def notices_for(self, case_id: int) -> list[Notice]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM notices WHERE case_id = ? ORDER BY id", (case_id,)
            ).fetchall()
        return [Notice.model_validate(dict(r)) for r in rows]
