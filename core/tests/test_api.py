import pytest
from fakes import FakeGate, FakeGitHub, FakeRunner, InlineExecutor
from fastapi.testclient import TestClient

from swe_agent.api import create_app
from swe_agent.config import Thresholds
from swe_agent.pipeline import Pipeline
from swe_agent.service import JobService
from swe_agent.store import Store

AUTH = {"Authorization": "Bearer svc-token"}
URL = "https://github.com/octo/app/issues/1"


@pytest.fixture
def client() -> TestClient:
    store = Store(":memory:")
    pipeline = Pipeline(
        store=store,
        github=FakeGitHub(),
        runner=FakeRunner(),
        gate=FakeGate(),
        thresholds=Thresholds(),
    )
    service = JobService(
        pipeline=pipeline, store=store, allowlist=["octo/app"], executor=InlineExecutor()
    )
    return TestClient(create_app(service, api_token="svc-token"))


def test_post_job_returns_202_and_get_returns_final_state(client: TestClient) -> None:
    r = client.post("/jobs", json={"issue_url": URL, "job_key": "k1"}, headers=AUTH)

    assert r.status_code == 202
    assert r.json()["key"] == "k1"
    got = client.get("/jobs/k1", headers=AUTH)
    assert got.status_code == 200
    body = got.json()
    assert body["state"] == "done"
    assert body["pr_url"] == "https://github.com/octo/app/pull/101"
    assert body["issue_url"] == URL


def test_requests_without_the_right_token_are_rejected(client: TestClient) -> None:
    body = {"issue_url": URL, "job_key": "k1"}

    assert client.post("/jobs", json=body).status_code == 401
    assert client.post("/jobs", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert client.get("/jobs/k1").status_code == 401


def test_repo_outside_the_allowlist_is_forbidden(client: TestClient) -> None:
    r = client.post(
        "/jobs",
        json={"issue_url": "https://github.com/evil/repo/issues/1", "job_key": "k"},
        headers=AUTH,
    )

    assert r.status_code == 403


def test_invalid_issue_url_is_unprocessable(client: TestClient) -> None:
    r = client.post(
        "/jobs", json={"issue_url": "https://github.com/octo/app/pull/1", "job_key": "k"}, headers=AUTH
    )

    assert r.status_code == 422


def test_conflicting_key_is_409(client: TestClient) -> None:
    client.post("/jobs", json={"issue_url": URL, "job_key": "k1"}, headers=AUTH)

    r = client.post(
        "/jobs",
        json={"issue_url": "https://github.com/octo/app/issues/2", "job_key": "k1"},
        headers=AUTH,
    )

    assert r.status_code == 409


def test_unknown_job_is_404(client: TestClient) -> None:
    assert client.get("/jobs/missing", headers=AUTH).status_code == 404


def test_healthz_needs_no_token(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_openapi_schema_describes_jobs_for_the_gateway(client: TestClient) -> None:
    schema = client.get("/openapi.json").json()

    assert "/jobs" in schema["paths"] and "/jobs/{key}" in schema["paths"]
    assert set(schema["components"]["schemas"]["JobResponse"]["properties"]) >= {
        "key",
        "issue_url",
        "state",
        "pr_url",
        "reason",
    }


def test_empty_api_token_is_refused_at_startup() -> None:
    with pytest.raises(ValueError, match="api_token"):
        create_app(None, api_token="")  # pyright: ignore[reportArgumentType]
