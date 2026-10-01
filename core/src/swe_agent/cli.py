import json
import uuid
from pathlib import Path
from typing import Annotated

import typer

from swe_agent.api import create_app, openapi_schema
from swe_agent.cli_watchdog import watchdog_app
from swe_agent.config import load_settings
from swe_agent.models import IssueRef, JobState
from swe_agent.service import RepoNotAllowed, ensure_allowed
from swe_agent.wiring import ConfigError, build_pipeline, build_service

app = typer.Typer(no_args_is_help=True, help="AI software engineer agent and watchdog.")

app.add_typer(watchdog_app, name="watchdog")

EXIT_CODES = {JobState.DONE: 0, JobState.ESCALATED: 2, JobState.FAILED: 1}


@app.command()
def fix(
    issue_url: Annotated[str, typer.Argument(help="https://github.com/OWNER/REPO/issues/N")],
    job_key: Annotated[str | None, typer.Option(help="Idempotency key; default is random.")] = None,
) -> None:
    """Resolve one issue now, in this process. Exit 0 done, 2 escalated, 1 failed."""
    try:
        ref = IssueRef.parse(issue_url)
    except ValueError as exc:
        raise typer.BadParameter(str(exc)) from exc
    settings = load_settings()
    try:
        ensure_allowed(ref, settings.repo_allowlist)
        pipeline, _ = build_pipeline(settings)
    except (RepoNotAllowed, ConfigError) as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    key = job_key or f"cli:{ref.full_name}#{ref.number}:{uuid.uuid4().hex[:8]}"
    job = pipeline.run(ref, key)
    typer.echo(f"state: {job.state.value}")
    if job.pr_url:
        typer.echo(f"pr: {job.pr_url}")
    if job.reason:
        typer.echo(f"reason: {job.reason}")
    raise typer.Exit(EXIT_CODES.get(job.state, 1))


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Run the HTTP API that the webhook gateway calls."""
    import uvicorn

    settings = load_settings()
    if settings.api_token is None:
        typer.echo("error: set SWE_AGENT_API_TOKEN to protect the API", err=True)
        raise typer.Exit(1)
    try:
        service = build_service(settings)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc
    for job in service.start():
        typer.echo(f"recovered interrupted job {job.key}")
    uvicorn.run(create_app(service, api_token=settings.api_token.get_secret_value()), host=host, port=port)


@app.command()
def openapi(out: Annotated[Path | None, typer.Option(help="Write here instead of stdout.")] = None) -> None:
    """Print the HTTP API's OpenAPI schema (the gateway's type source)."""
    text = json.dumps(openapi_schema(), indent=2, sort_keys=True)
    if out is None:
        typer.echo(text)
    else:
        out.write_text(text + "\n")
