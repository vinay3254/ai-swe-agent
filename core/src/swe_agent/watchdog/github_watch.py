import base64
import binascii

from swe_agent.github import GitHubClient, GitHubError
from swe_agent.watchdog.models import Candidate, Source

MAX_FORK_PAGES = 5  # 500 forks per scan; later pages wait for the next run
MAX_FILE_CHARS = 200_000


class WatchGitHubClient(GitHubClient):
    """Read-only GitHub queries for the watchdog, plus the one write it may make: a notice issue."""

    def authenticated_login(self) -> str:
        return str(self._request("GET", "/user")["login"])

    def list_forks(self, repo: str) -> list[Candidate]:
        forks: list[Candidate] = []
        for page in range(1, MAX_FORK_PAGES + 1):
            batch = self._request(
                "GET", f"/repos/{repo}/forks", params={"per_page": "100", "page": str(page)}
            )
            forks.extend(
                Candidate(
                    repo=f["full_name"],
                    source=Source.FORK,
                    html_url=f["html_url"],
                    pushed_at=f.get("pushed_at"),
                )
                for f in batch
            )
            if len(batch) < 100:
                break
        return forks

    def list_collaborators(self, repo: str) -> set[str] | None:
        """Lowercased logins, or None when the token cannot see the collaborator list."""
        try:
            data = self._request("GET", f"/repos/{repo}/collaborators", params={"per_page": "100"})
        except GitHubError as exc:
            if exc.status in (403, 404):
                return None
            raise
        return {str(c["login"]).lower() for c in data}

    def search_code(self, line: str, *, exclude_owner: str) -> list[Candidate]:
        query = f'"{line.replace(chr(34), " ")}" -user:{exclude_owner}'
        data = self._request("GET", "/search/code", params={"q": query, "per_page": "30"})
        seen: dict[str, Candidate] = {}
        for item in data.get("items", []):
            repo = item["repository"]
            seen.setdefault(
                repo["full_name"],
                Candidate(repo=repo["full_name"], source=Source.CODE_SEARCH, html_url=repo["html_url"]),
            )
        return list(seen.values())

    def get_text_file(self, repo: str, path: str) -> str | None:
        """File text, or None if missing, binary, or too large to read through the contents API."""
        try:
            data = self._request("GET", f"/repos/{repo}/contents/{path}")
        except GitHubError as exc:
            if exc.status == 404:
                return None
            raise
        payload: dict[str, str] = data if isinstance(data, dict) else {}  # pyright: ignore[reportUnknownVariableType]
        if payload.get("encoding") != "base64":
            return None
        try:
            text = base64.b64decode(payload["content"]).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        return text if len(text) <= MAX_FILE_CHARS else None

    def list_files(self, repo: str) -> dict[str, int]:
        """Blob path -> size for the default branch."""
        branch = self.default_branch(repo)
        tree = self._request("GET", f"/repos/{repo}/git/trees/{branch}", params={"recursive": "1"})
        return {e["path"]: int(e.get("size", 0)) for e in tree["tree"] if e["type"] == "blob"}

    def head_sha(self, repo: str) -> str | None:
        try:
            commits = self._request("GET", f"/repos/{repo}/commits", params={"per_page": "1"})
        except GitHubError as exc:
            if exc.status == 409:  # empty repository
                return None
            raise
        return str(commits[0]["sha"]) if commits else None

    def create_issue(self, repo: str, *, title: str, body: str) -> str:
        data = self._request("POST", f"/repos/{repo}/issues", json={"title": title, "body": body})
        return str(data["html_url"])
