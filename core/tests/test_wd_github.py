import base64

import httpx
import pytest
import respx

from swe_agent.github import GitHubError
from swe_agent.watchdog.github_watch import WatchGitHubClient
from swe_agent.watchdog.models import Source

API = "https://api.github.test"


@pytest.fixture
def gh() -> WatchGitHubClient:
    return WatchGitHubClient(token="t", base_url=API, sleep=lambda _: None)


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


@respx.mock
def test_list_forks_paginates_and_maps_candidates(gh: WatchGitHubClient) -> None:
    page1 = [
        {"full_name": f"u{i}/app", "html_url": f"https://github.com/u{i}/app", "pushed_at": "2026-01-01T00:00:00Z"}
        for i in range(100)
    ]
    page2 = [{"full_name": "last/app", "html_url": "https://github.com/last/app", "pushed_at": None}]
    route = respx.get(f"{API}/repos/octo/app/forks").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )

    forks = gh.list_forks("octo/app")

    assert len(forks) == 101
    assert forks[0].repo == "u0/app" and forks[0].source is Source.FORK
    assert forks[-1].pushed_at is None
    assert route.calls[1].request.url.params["page"] == "2"


@respx.mock
def test_collaborators_are_lowercased_logins_or_none_without_access(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/repos/octo/app/collaborators").mock(
        return_value=httpx.Response(200, json=[{"login": "Octo"}, {"login": "Helper"}])
    )
    assert gh.list_collaborators("octo/app") == {"octo", "helper"}

    respx.get(f"{API}/repos/octo/priv/collaborators").mock(
        return_value=httpx.Response(403, json={"message": "Must have push access"})
    )
    assert gh.list_collaborators("octo/priv") is None


@respx.mock
def test_search_code_excludes_the_owner_and_dedupes_repos(gh: WatchGitHubClient) -> None:
    route = respx.get(f"{API}/search/code").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"path": "a.py", "repository": {"full_name": "x/y", "html_url": "https://github.com/x/y"}},
                    {"path": "b.py", "repository": {"full_name": "x/y", "html_url": "https://github.com/x/y"}},
                    {"path": "c.py", "repository": {"full_name": "z/w", "html_url": "https://github.com/z/w"}},
                ]
            },
        )
    )

    found = gh.search_code('def compute_distinctive_checksum(payload, salt):', exclude_owner="octo")

    assert [(c.repo, c.source) for c in found] == [("x/y", Source.CODE_SEARCH), ("z/w", Source.CODE_SEARCH)]
    query = route.calls.last.request.url.params["q"]
    assert query == '"def compute_distinctive_checksum(payload, salt):" -user:octo'


@respx.mock
def test_get_text_file_decodes_base64_and_returns_none_for_404(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/repos/a/b/contents/LICENSE").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": b64("MIT\n")})
    )
    respx.get(f"{API}/repos/a/b/contents/NOTICE").mock(return_value=httpx.Response(404, json={}))

    assert gh.get_text_file("a/b", "LICENSE") == "MIT\n"
    assert gh.get_text_file("a/b", "NOTICE") is None


@respx.mock
def test_get_text_file_skips_binary_and_oversized_content(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/repos/a/b/contents/logo.png").mock(
        return_value=httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"\xff\xfe\x00").decode()})
    )
    respx.get(f"{API}/repos/a/b/contents/big.txt").mock(
        return_value=httpx.Response(200, json={"encoding": "none", "content": ""})
    )

    assert gh.get_text_file("a/b", "logo.png") is None
    assert gh.get_text_file("a/b", "big.txt") is None


@respx.mock
def test_list_files_returns_blob_paths_and_sizes(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/repos/a/b").mock(return_value=httpx.Response(200, json={"default_branch": "main"}))
    respx.get(f"{API}/repos/a/b/git/trees/main").mock(
        return_value=httpx.Response(
            200,
            json={
                "tree": [
                    {"type": "blob", "path": "src/a.py", "size": 120},
                    {"type": "tree", "path": "src"},
                    {"type": "blob", "path": "LICENSE", "size": 1000},
                ]
            },
        )
    )

    assert gh.list_files("a/b") == {"src/a.py": 120, "LICENSE": 1000}


@respx.mock
def test_head_sha_and_empty_repo(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/repos/a/b/commits").mock(return_value=httpx.Response(200, json=[{"sha": "abc123"}]))
    respx.get(f"{API}/repos/a/empty/commits").mock(return_value=httpx.Response(409, json={"message": "empty"}))

    assert gh.head_sha("a/b") == "abc123"
    assert gh.head_sha("a/empty") is None


@respx.mock
def test_create_issue_returns_the_html_url(gh: WatchGitHubClient) -> None:
    route = respx.post(f"{API}/repos/x/y/issues").mock(
        return_value=httpx.Response(201, json={"html_url": "https://github.com/x/y/issues/3"})
    )

    url = gh.create_issue("x/y", title="T", body="B")

    assert url == "https://github.com/x/y/issues/3"
    assert route.calls.last.request.content == b'{"title":"T","body":"B"}'


@respx.mock
def test_create_issue_on_a_repo_with_issues_disabled_raises(gh: WatchGitHubClient) -> None:
    respx.post(f"{API}/repos/x/y/issues").mock(
        return_value=httpx.Response(410, json={"message": "Issues are disabled for this repo"})
    )

    with pytest.raises(GitHubError, match="410.*disabled"):
        gh.create_issue("x/y", title="T", body="B")


@respx.mock
def test_authenticated_login(gh: WatchGitHubClient) -> None:
    respx.get(f"{API}/user").mock(return_value=httpx.Response(200, json={"login": "octo"}))
    assert gh.authenticated_login() == "octo"
