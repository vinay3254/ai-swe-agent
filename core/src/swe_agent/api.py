import hmac
from typing import Annotated, Any, cast

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, field_validator

from swe_agent.models import IssueRef, JobState
from swe_agent.service import JobConflict, JobService, RepoNotAllowed


class JobRequest(BaseModel):
    issue_url: str
    job_key: str

    @field_validator("issue_url")
    @classmethod
    def _must_be_issue_url(cls, value: str) -> str:
        IssueRef.parse(value)  # raises ValueError, which FastAPI reports as 422
        return value

    @field_validator("job_key")
    @classmethod
    def _key_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("job_key must not be blank")
        return value


class JobResponse(BaseModel):
    key: str
    issue_url: str
    state: JobState
    pr_url: str | None
    reason: str | None


def create_app(service: JobService, *, api_token: str) -> FastAPI:
    """HTTP API. Its OpenAPI schema is the source of truth for the gateway's types."""
    if not api_token:
        raise ValueError("api_token must not be empty")
    app = FastAPI(title="ai-swe-agent core", version="0.1.0")

    def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
        expected = f"Bearer {api_token}"
        if authorization is None or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="missing or invalid bearer token")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/jobs", status_code=202, response_model=JobResponse, dependencies=[Depends(require_token)])
    def create_job(request: JobRequest) -> JobResponse:
        try:
            job = service.submit(IssueRef.parse(request.issue_url), request.job_key)
        except RepoNotAllowed as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except JobConflict as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return JobResponse.model_validate(job.model_dump())

    @app.get("/jobs/{key}", response_model=JobResponse, dependencies=[Depends(require_token)])
    def get_job(key: str) -> JobResponse:
        job = service.get(key)
        if job is None:
            raise HTTPException(status_code=404, detail="no such job")
        return JobResponse.model_validate(job.model_dump())

    return app


def openapi_schema() -> dict[str, Any]:
    """The API contract, without a running service. Handlers are never called here."""
    return create_app(cast(JobService, None), api_token="schema-only").openapi()
