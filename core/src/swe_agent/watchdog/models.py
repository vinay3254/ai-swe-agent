from enum import StrEnum

from pydantic import BaseModel, Field

from swe_agent.watchdog.fingerprint import FileMatch
from swe_agent.watchdog.license_check import LicenseCheck


class Source(StrEnum):
    FORK = "fork"
    CODE_SEARCH = "code_search"


class Verdict(StrEnum):
    VIOLATION = "violation"
    REVIEW = "review"  # low confidence: a human decides, nothing is sent
    CLEAR = "clear"


class CaseStatus(StrEnum):
    OPEN = "open"  # violation or review item waiting for the owner
    DISMISSED = "dismissed"
    NOTIFIED = "notified"
    CLOSED = "closed"  # assessed clear


class Candidate(BaseModel, frozen=True):
    repo: str  # owner/name
    source: Source
    html_url: str
    pushed_at: str | None = None


class VerdictResult(BaseModel, frozen=True):
    verdict: Verdict
    reason: str


class Evidence(BaseModel, frozen=True):
    original_repo: str
    candidate_repo: str
    candidate_url: str
    source: Source
    matched_files: list[FileMatch] = Field(default_factory=lambda: list[FileMatch]())
    head_sha: str | None = None
    checked_at: str
    license_check: LicenseCheck | None = None
    original_copyright: list[str] = Field(default_factory=lambda: list[str]())
    derived: bool | None = None
    derived_confidence: float | None = None
    similarity: float | None = None
    attribution_ok: bool | None = None
    attribution_confidence: float | None = None
    reason: str
    notes: list[str] = Field(default_factory=lambda: list[str]())


class Case(BaseModel, frozen=True):
    id: int
    original_repo: str
    candidate_repo: str
    verdict: Verdict
    status: CaseStatus
    pushed_at: str | None
    evidence: Evidence
    created_at: str
    updated_at: str
