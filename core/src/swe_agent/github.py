import time
from collections.abc import Callable
from typing import Any

import httpx

from swe_agent.models import Issue, IssueRef

RETRY_STATUS = frozenset({500, 502, 503, 504})


class GitHubError(Exception):
    """A GitHub call failed for a reason a retry cannot fix, or retries ran out."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class GitHubClient:
    """Typed REST adapter that implements `GitHubPort`."""

    def __init__(
        self,
        *,
        token: str,
        base_url: str = "https://api.github.com",
        timeout_s: float = 30.0,
        max_attempts: int = 3,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
        max_rate_wait_s: float = 120.0,
    ) -> None:
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._now = now
        self._max_rate_wait_s = max_rate_wait_s
        self._http = httpx.Client(
            base_url=base_url,
            timeout=timeout_s,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    def close(self) -> None:
        self._http.close()

    def _request(
        self, method: str, path: str, *, json: object = None, params: dict[str, str] | None = None
    ) -> Any:
        last = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.request(method, path, json=json, params=params)
            except httpx.TransportError as exc:
                last = repr(exc)
            else:
                wait = self._rate_limit_wait(response)
                if wait is not None:
                    if wait > self._max_rate_wait_s:
                        raise GitHubError(f"{method} {path} hit the rate limit; resets in {wait:.0f}s")
                    last = "rate limited"
                    if attempt < self._max_attempts:
                        self._sleep(wait)
                    continue
                if response.status_code in RETRY_STATUS:
                    last = f"HTTP {response.status_code}"
                elif response.is_success:
                    return response.json() if response.content else None
                else:
                    message = _message(response)
                    raise GitHubError(
                        f"{method} {path} failed: {response.status_code} {message}", response.status_code
                    )
            if attempt < self._max_attempts:
                self._sleep(min(0.5 * 2 ** (attempt - 1), 8.0))
        raise GitHubError(f"{method} {path} failed after {self._max_attempts} attempts: {last}")

    def _rate_limit_wait(self, response: httpx.Response) -> float | None:
        """Seconds to wait if this response is a rate limit, else None. A plain 403 is not one."""
        if response.status_code not in (403, 429):
            return None
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            return float(retry_after)
        if response.headers.get("X-RateLimit-Remaining") == "0":
            reset = float(response.headers.get("X-RateLimit-Reset", "0"))
            return max(reset - self._now(), 0.0) + 1.0
        return None

    def fetch_issue(self, ref: IssueRef) -> Issue:
        data = self._request("GET", f"/repos/{ref.full_name}/issues/{ref.number}")
        if "pull_request" in data:
            raise GitHubError(f"{ref.url} is a pull request, not an issue")
        return Issue(
            ref=ref,
            title=data["title"],
            body=data.get("body") or "",
            labels=tuple(label["name"] for label in data.get("labels", [])),
        )

    def comment(self, ref: IssueRef, body: str) -> None:
        self._request("POST", f"/repos/{ref.full_name}/issues/{ref.number}/comments", json={"body": body})

    def add_label(self, ref: IssueRef, label: str) -> None:
        self._request("POST", f"/repos/{ref.full_name}/issues/{ref.number}/labels", json={"labels": [label]})

    def default_branch(self, full_name: str) -> str:
        data = self._request("GET", f"/repos/{full_name}")
        return str(data["default_branch"])

    def open_pr(
        self, ref: IssueRef, *, branch: str, title: str, body: str, draft: bool, labels: list[str]
    ) -> str:
        """Open a PR from `branch`, or return the open PR that already exists for it."""
        existing = self._request(
            "GET",
            f"/repos/{ref.full_name}/pulls",
            params={"head": f"{ref.owner}:{branch}", "state": "open"},
        )
        if existing:
            return str(existing[0]["html_url"])
        created = self._request(
            "POST",
            f"/repos/{ref.full_name}/pulls",
            json={
                "title": title,
                "head": branch,
                "base": self.default_branch(ref.full_name),
                "body": body,
                "draft": draft,
            },
        )
        if labels:
            self._request(
                "POST", f"/repos/{ref.full_name}/issues/{created['number']}/labels", json={"labels": labels}
            )
        return str(created["html_url"])


def _message(response: httpx.Response) -> str:
    try:
        return str(response.json().get("message", response.text))
    except ValueError:
        return response.text
