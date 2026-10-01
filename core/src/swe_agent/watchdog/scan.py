import logging
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Protocol

from pydantic import BaseModel, Field

from swe_agent.config import Thresholds
from swe_agent.github import GitHubError
from swe_agent.jev_gate.client import JevError
from swe_agent.jev_gate.decision import Decision
from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.fingerprint import FileMatch, Fingerprints, build_fingerprints, match_files
from swe_agent.watchdog.gate import WatchGate
from swe_agent.watchdog.license_check import check_attribution, extract_copyright_lines
from swe_agent.watchdog.models import Candidate, Case, CaseStatus, Evidence, Source, Verdict, VerdictResult
from swe_agent.watchdog.verdict import decide

log = logging.getLogger(__name__)

LICENSE_NAMES = ("LICENSE", "LICENSE.md", "LICENSE.txt", "COPYING", "NOTICE")
NOTICE_NAMES = (*LICENSE_NAMES, "README.md", "README")
CODE_SUFFIXES = frozenset(
    {".py", ".js", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java", ".kt", ".c", ".h", ".cpp", ".cc",
     ".hpp", ".rb", ".php", ".cs", ".swift", ".scala"}
)
MAX_ORIGINAL_FILES = 8
MAX_FILE_BYTES = 100_000
LIMITS = [
    "Public GitHub repositories only: private repositories and non-GitHub hosts are not visible.",
    "GitHub traffic data is aggregate for 14 days and names no one, so who cloned cannot be known.",
]


class WatchGitHub(Protocol):
    def list_forks(self, repo: str) -> list[Candidate]: ...
    def list_collaborators(self, repo: str) -> set[str] | None: ...
    def search_code(self, line: str, *, exclude_owner: str) -> list[Candidate]: ...
    def get_text_file(self, repo: str, path: str) -> str | None: ...
    def list_files(self, repo: str) -> dict[str, int]: ...
    def head_sha(self, repo: str) -> str | None: ...


class ScanReport(BaseModel):
    repo: str
    candidates_seen: int = 0
    skipped_unchanged: int = 0
    cases: list[Case] = Field(default_factory=lambda: list[Case]())
    errors: list[str] = Field(default_factory=lambda: list[str]())
    notes: list[str] = Field(default_factory=lambda: list[str]())

    @property
    def violations(self) -> list[Case]:
        return [c for c in self.cases if c.verdict is Verdict.VIOLATION and c.status is CaseStatus.OPEN]

    @property
    def reviews(self) -> list[Case]:
        return [c for c in self.cases if c.verdict is Verdict.REVIEW and c.status is CaseStatus.OPEN]


class _Original(BaseModel):
    repo: str
    copyright_lines: list[str]
    sources: dict[str, str]
    fingerprints: Fingerprints


class Watchdog:
    def __init__(
        self, *, github: WatchGitHub, gate: WatchGate, cases: CaseStore, thresholds: Thresholds
    ) -> None:
        self._gh = github
        self._gate = gate
        self._cases = cases
        self._t = thresholds

    def scan(self, repo: str) -> ScanReport:
        """Assess forks and code-search matches of `repo`. Read-only: it never contacts anyone."""
        report = ScanReport(repo=repo, notes=list(LIMITS))
        original = self._load_original(repo, report)
        if original is None:
            return report
        owner = repo.split("/")[0].lower()

        collaborators = self._gh.list_collaborators(repo)
        if collaborators is None:
            report.notes.append(
                "collaborator list unavailable to this token: only the repo owner is trusted"
            )
        trusted = {owner} | (collaborators or set())

        candidates: dict[str, Candidate] = {}
        for fork in self._gh.list_forks(repo):
            candidates.setdefault(fork.repo, fork)
        for line in original.fingerprints.lines:
            try:
                hits = self._gh.search_code(line, exclude_owner=owner)
            except GitHubError as exc:
                report.errors.append(f"code search failed: {exc}")
                break
            for hit in hits:
                candidates.setdefault(hit.repo, hit)

        for candidate in candidates.values():
            if candidate.repo.split("/")[0].lower() in trusted:
                continue
            report.candidates_seen += 1
            if (
                candidate.pushed_at is not None
                and self._cases.seen_pushed_at(repo, candidate.repo) == candidate.pushed_at
            ):
                report.skipped_unchanged += 1
                continue
            try:
                report.cases.append(self._assess(repo, candidate, original))
            except (GitHubError, JevError) as exc:  # one bad candidate must not stop the scan
                log.warning("could not assess %s: %s", candidate.repo, exc)
                report.errors.append(f"{candidate.repo}: {exc}")
        return report

    def _load_original(self, repo: str, report: ScanReport) -> _Original | None:
        license_text = next(
            (t for name in LICENSE_NAMES if (t := self._gh.get_text_file(repo, name)) is not None), None
        )
        lines = extract_copyright_lines(license_text or "")
        if not lines:
            report.notes.append(
                f"{repo} has no LICENSE with a dated copyright line, so there is no attribution to enforce"
            )
            return None
        sizes = self._gh.list_files(repo)
        paths = sorted(
            (p for p, size in sizes.items() if PurePosixPath(p).suffix in CODE_SUFFIXES and size <= MAX_FILE_BYTES),
            key=lambda p: (-sizes[p], p),
        )[:MAX_ORIGINAL_FILES]
        sources = {p: t for p in paths if (t := self._gh.get_text_file(repo, p)) is not None}
        fingerprints = build_fingerprints(sources)
        if not fingerprints.lines:
            report.notes.append("no distinctive lines found in the original, so code search was skipped")
        return _Original(repo=repo, copyright_lines=lines, sources=sources, fingerprints=fingerprints)

    def _assess(self, repo: str, candidate: Candidate, original: _Original) -> Case:
        gh = self._gh
        notice_files = {
            name: text for name in NOTICE_NAMES if (text := gh.get_text_file(candidate.repo, name)) is not None
        }
        license_check = check_attribution(original.copyright_lines, notice_files)

        matches: list[FileMatch] = []
        derived: Decision[bool] | None = None
        attribution: Decision[bool] | None = None
        candidate_sources: dict[str, str] = {}
        if not license_check.preserved:
            candidate_sources = {
                p: t for p in original.sources if (t := gh.get_text_file(candidate.repo, p)) is not None
            }
            matches = match_files(original.fingerprints, candidate_sources)
            if candidate.source is Source.CODE_SEARCH and matches:
                derived = self._derived(matches, original, candidate_sources)
            sure_derived = candidate.source is Source.FORK or (
                derived is not None and derived.value and derived.confidence >= self._t.watchdog_min_confidence
            )
            if license_check.has_notice_text and sure_derived:
                notice_text = "\n\n".join(
                    text for name, text in notice_files.items() if name in LICENSE_NAMES
                )
                attribution = self._gate.attribution(original.copyright_lines, notice_text)

        if candidate.source is Source.CODE_SEARCH and not license_check.preserved and not matches:
            result = VerdictResult(
                verdict=Verdict.CLEAR, reason="only incidental line matches: no file is a copy"
            )
        else:
            result = decide(
                source=candidate.source,
                license=license_check,
                derived=derived,
                attribution=attribution,
                min_confidence=self._t.watchdog_min_confidence,
            )

        evidence = Evidence(
            original_repo=repo,
            candidate_repo=candidate.repo,
            candidate_url=candidate.html_url,
            source=candidate.source,
            matched_files=matches,
            head_sha=gh.head_sha(candidate.repo),
            checked_at=datetime.now(UTC).isoformat(),
            license_check=license_check,
            original_copyright=original.copyright_lines,
            derived=None if derived is None else derived.value,
            derived_confidence=None if derived is None else derived.confidence,
            attribution_ok=None if attribution is None else attribution.value,
            attribution_confidence=None if attribution is None else attribution.confidence,
            reason=result.reason,
        )
        return self._cases.record(repo, candidate.repo, result.verdict, candidate.pushed_at, evidence)

    def _derived(
        self, matches: list[FileMatch], original: _Original, candidate_sources: dict[str, str]
    ) -> Decision[bool]:
        if any(m.kind == "hash" for m in matches):  # an identical file is derived, no judgment needed
            return Decision(value=True, confidence=1.0, probabilities={"true": 1.0, "false": 0.0})
        path = matches[0].path
        return self._gate.derivation(original.sources[path], candidate_sources[path]).derived
