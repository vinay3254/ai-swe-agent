from dataclasses import dataclass, field

from swe_agent.github import GitHubError
from swe_agent.jev_gate.decision import Decision, noul_decision
from swe_agent.watchdog.gate import Derivation
from swe_agent.watchdog.models import Candidate, Source

CORE = (
    "import os\n"
    "def compute_distinctive_checksum(payload, salt):\n"
    "    return hashlib.sha256(payload + salt + b'swe-agent-demo').hexdigest()\n"
    "def another_unusually_long_function_name_for_fingerprints(value): return value * 31\n"
)
MIT = "MIT License\n\nCopyright (c) 2024 Jane Doe\n\nPermission is hereby granted...\n"
ORIGINAL_FILES = {"src/core.py": CORE, "LICENSE": MIT, "README.md": "# app\n"}


@dataclass
class FakeWatchGitHub:
    repos: dict[str, dict[str, str]] = field(default_factory=dict)
    forks: list[Candidate] = field(default_factory=list)
    collaborators: set[str] | None = field(default_factory=lambda: {"jane"})
    search_hits: list[Candidate] = field(default_factory=list)
    errors: dict[str, Exception] = field(default_factory=dict)
    issues_created: list[tuple[str, str, str]] = field(default_factory=list)
    searches: list[str] = field(default_factory=list)
    login: str = "jane"

    def authenticated_login(self) -> str:
        return self.login

    def list_forks(self, repo: str) -> list[Candidate]:
        return self.forks

    def list_collaborators(self, repo: str) -> set[str] | None:
        return self.collaborators

    def search_code(self, line: str, *, exclude_owner: str) -> list[Candidate]:
        self.searches.append(line)
        return self.search_hits

    def get_text_file(self, repo: str, path: str) -> str | None:
        if repo in self.errors:
            raise self.errors[repo]
        return self.repos.get(repo, {}).get(path)

    def list_files(self, repo: str) -> dict[str, int]:
        return {path: len(text) for path, text in self.repos.get(repo, {}).items()}

    def head_sha(self, repo: str) -> str | None:
        return f"sha-{repo}"

    def create_issue(self, repo: str, *, title: str, body: str) -> str:
        if repo in self.errors:
            raise self.errors[repo]
        self.issues_created.append((repo, title, body))
        return f"https://github.com/{repo}/issues/1"


@dataclass
class FakeWatchGate:
    derived_p: float = 0.99
    attributed_p: float = 0.01
    derivation_calls: int = 0
    attribution_calls: int = 0

    def derivation(self, original: str, candidate: str) -> Derivation:
        self.derivation_calls += 1
        return Derivation(
            derived=noul_decision(self.derived_p),
            similarity=Decision(value=3.0, confidence=0.9, probabilities={}),
        )

    def attribution(self, original_copyright: list[str], candidate_notice: str) -> Decision[bool]:
        self.attribution_calls += 1
        return noul_decision(self.attributed_p)


def fork(repo: str, pushed_at: str = "2026-09-01T00:00:00Z") -> Candidate:
    return Candidate(repo=repo, source=Source.FORK, html_url=f"https://github.com/{repo}", pushed_at=pushed_at)


def hit(repo: str) -> Candidate:
    return Candidate(repo=repo, source=Source.CODE_SEARCH, html_url=f"https://github.com/{repo}")


def boom(status: int = 500) -> GitHubError:
    return GitHubError("GitHub exploded", status)
