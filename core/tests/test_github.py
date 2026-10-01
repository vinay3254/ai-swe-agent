import json

import httpx
import pytest
import respx

from swe_agent.github import GitHubClient, GitHubError
from swe_agent.models import IssueRef

API = "https://api.github.test"
REF = IssueRef(owner="octo", repo="app", number=7)


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def gh(sleeps: list[float]) -> GitHubClient:
    return GitHubClient(token="tok", base_url=API, max_attempts=3, sleep=sleeps.append)


@respx.mock
def test_fetch_issue_maps_fields_and_sends_auth(gh: GitHubClient) -> None:
    route = respx.get(f"{API}/repos/octo/app/issues/7").mock(
        return_value=httpx.Response(
            200,
            json={"title": "Crash", "body": "details", "labels": [{"name": "bug"}, {"name": "ai-fix"}]},
        )
    )

    issue = gh.fetch_issue(REF)

    assert (issue.title, issue.body, issue.labels) == ("Crash", "details", ("bug", "ai-fix"))
    assert route.calls.last.request.headers["Authorization"] == "Bearer tok"


@respx.mock
def test_fetch_issue_with_null_body_becomes_empty_string(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app/issues/7").mock(
        return_value=httpx.Response(200, json={"title": "t", "body": None, "labels": []})
    )

    assert gh.fetch_issue(REF).body == ""


@respx.mock
def test_fetch_issue_rejects_pull_requests(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app/issues/7").mock(
        return_value=httpx.Response(
            200, json={"title": "t", "body": "", "labels": [], "pull_request": {"url": "x"}}
        )
    )

    with pytest.raises(GitHubError, match="pull request"):
        gh.fetch_issue(REF)


@respx.mock
def test_comment_and_label_post_to_issue_endpoints(gh: GitHubClient) -> None:
    c = respx.post(f"{API}/repos/octo/app/issues/7/comments").mock(
        return_value=httpx.Response(201, json={})
    )
    l = respx.post(f"{API}/repos/octo/app/issues/7/labels").mock(
        return_value=httpx.Response(200, json=[])
    )

    gh.comment(REF, "hello")
    gh.add_label(REF, "needs-human")

    assert json.loads(c.calls.last.request.content) == {"body": "hello"}
    assert json.loads(l.calls.last.request.content) == {"labels": ["needs-human"]}


@respx.mock
def test_open_pr_targets_default_branch_and_labels_the_pr(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app").mock(
        return_value=httpx.Response(200, json={"default_branch": "trunk"})
    )
    respx.get(f"{API}/repos/octo/app/pulls").mock(return_value=httpx.Response(200, json=[]))
    pr = respx.post(f"{API}/repos/octo/app/pulls").mock(
        return_value=httpx.Response(
            201, json={"number": 12, "html_url": "https://github.com/octo/app/pull/12"}
        )
    )
    lab = respx.post(f"{API}/repos/octo/app/issues/12/labels").mock(
        return_value=httpx.Response(200, json=[])
    )

    url = gh.open_pr(
        REF, branch="ai-fix/issue-7", title="Fix #7", body="b", draft=True, labels=["incomplete"]
    )

    assert url == "https://github.com/octo/app/pull/12"
    assert json.loads(pr.calls.last.request.content) == {
        "title": "Fix #7",
        "head": "ai-fix/issue-7",
        "base": "trunk",
        "body": "b",
        "draft": True,
    }
    assert json.loads(lab.calls.last.request.content) == {"labels": ["incomplete"]}


@respx.mock
def test_open_pr_reuses_an_existing_open_pr_for_the_branch(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app").mock(
        return_value=httpx.Response(200, json={"default_branch": "main"})
    )
    listing = respx.get(f"{API}/repos/octo/app/pulls").mock(
        return_value=httpx.Response(
            200, json=[{"number": 3, "html_url": "https://github.com/octo/app/pull/3"}]
        )
    )
    create = respx.post(f"{API}/repos/octo/app/pulls").mock(return_value=httpx.Response(500))

    url = gh.open_pr(REF, branch="ai-fix/issue-7", title="t", body="b", draft=False, labels=[])

    assert url == "https://github.com/octo/app/pull/3"
    assert listing.calls.last.request.url.params["head"] == "octo:ai-fix/issue-7"
    assert create.call_count == 0


@respx.mock
def test_server_errors_are_retried_then_succeed(gh: GitHubClient, sleeps: list[float]) -> None:
    route = respx.post(f"{API}/repos/octo/app/issues/7/comments").mock(
        side_effect=[httpx.Response(502), httpx.Response(201, json={})]
    )

    gh.comment(REF, "x")

    assert route.call_count == 2
    assert sleeps == [0.5]


@respx.mock
def test_client_errors_raise_with_status_and_are_not_retried(gh: GitHubClient) -> None:
    route = respx.post(f"{API}/repos/octo/app/issues/7/comments").mock(
        return_value=httpx.Response(403, json={"message": "Resource not accessible"})
    )

    with pytest.raises(GitHubError, match="403.*Resource not accessible"):
        gh.comment(REF, "x")

    assert route.call_count == 1


@respx.mock
def test_exhausted_retries_raise(gh: GitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app/issues/7").mock(return_value=httpx.Response(503))

    with pytest.raises(GitHubError, match="3 attempts"):
        gh.fetch_issue(REF)


@respx.mock
def test_rate_limit_with_retry_after_waits_then_retries(sleeps: list[float]) -> None:
    gh = GitHubClient(token="t", base_url=API, max_attempts=3, sleep=sleeps.append)
    route = respx.post(f"{API}/repos/octo/app/issues/7/comments").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "2"}, json={"message": "slow down"}),
            httpx.Response(201, json={}),
        ]
    )

    gh.comment(REF, "x")

    assert route.call_count == 2
    assert sleeps == [2.0]


@respx.mock
def test_primary_rate_limit_waits_until_the_reset_time(sleeps: list[float]) -> None:
    gh = GitHubClient(token="t", base_url=API, max_attempts=3, sleep=sleeps.append, now=lambda: 1000.0)
    route = respx.get(f"{API}/repos/octo/app/issues/7").mock(
        side_effect=[
            httpx.Response(
                403,
                headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": "1005"},
                json={"message": "API rate limit exceeded"},
            ),
            httpx.Response(200, json={"title": "t", "body": "", "labels": []}),
        ]
    )

    gh.fetch_issue(REF)

    assert route.call_count == 2
    assert sleeps == [6.0]  # 5 seconds to reset, plus 1 of slack


@respx.mock
def test_rate_limit_longer_than_the_cap_raises_instead_of_hanging(sleeps: list[float]) -> None:
    gh = GitHubClient(
        token="t", base_url=API, max_attempts=3, sleep=sleeps.append, max_rate_wait_s=60.0
    )
    respx.get(f"{API}/repos/octo/app/issues/7").mock(
        return_value=httpx.Response(429, headers={"Retry-After": "600"})
    )

    with pytest.raises(GitHubError, match="rate limit.*600"):
        gh.fetch_issue(REF)

    assert sleeps == []


@respx.mock
def test_plain_403_is_still_a_permission_error_not_a_rate_limit(sleeps: list[float]) -> None:
    gh = GitHubClient(token="t", base_url=API, max_attempts=3, sleep=sleeps.append)
    route = respx.get(f"{API}/repos/octo/app/issues/7").mock(
        return_value=httpx.Response(403, json={"message": "Forbidden"})
    )

    with pytest.raises(GitHubError, match="403"):
        gh.fetch_issue(REF)

    assert route.call_count == 1 and sleeps == []
