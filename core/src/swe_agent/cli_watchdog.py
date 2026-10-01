import json
from pathlib import Path
from typing import Annotated

import typer

from swe_agent.config import Settings, load_settings
from swe_agent.github import GitHubError
from swe_agent.watchdog.digest import render_digest, send_email
from swe_agent.watchdog.models import CaseStatus
from swe_agent.watchdog.notice import (
    NoticeBlocked,
    ensure_sendable,
    render_dmca_draft,
    render_issue_notice,
    send_issue_notice,
)
from swe_agent.wiring import ConfigError, WatchdogRuntime, build_watchdog

watchdog_app = typer.Typer(no_args_is_help=True, help="Find license-violating copies of your repos.")


def _runtime() -> tuple[Settings, WatchdogRuntime]:
    settings = load_settings()
    try:
        return settings, build_watchdog(settings)
    except ConfigError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(1) from exc


def _fail(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(1)


@watchdog_app.command()
def scan(
    repo: Annotated[list[str] | None, typer.Option(help="OWNER/REPO to scan; default: the allowlist.")] = None,
    email: Annotated[bool, typer.Option(help="Email the digest instead of only printing it.")] = False,
) -> None:
    """Scan for forks and copies, record cases, and print a digest. Contacts no one."""
    settings, rt = _runtime()
    repos = repo or settings.repo_allowlist
    if not repos:
        raise _fail("no repos to scan: pass --repo or set SWE_AGENT_REPO_ALLOWLIST")
    allowed = {r.lower() for r in settings.repo_allowlist}
    for name in repos:
        if name.lower() not in allowed:
            raise _fail(f"{name} is not in the repo allowlist")
        report = rt.watchdog.scan(name)
        typer.echo(
            f"{name}: {report.candidates_seen} candidates, {report.skipped_unchanged} unchanged, "
            f"{len(report.violations)} violation(s), {len(report.reviews)} to review"
        )
        for error in report.errors:
            typer.echo(f"  error: {error}", err=True)
        for note in report.notes:
            typer.echo(f"  note: {note}")
    subject, body = render_digest(
        rt.cases.list_cases(status=CaseStatus.OPEN), owner_login=rt.github.authenticated_login()
    )
    typer.echo(f"\n{subject}\n\n{body}")
    if email:
        if not (settings.smtp_host and settings.digest_to and settings.smtp_from):
            raise _fail("set SWE_AGENT_SMTP_HOST, SWE_AGENT_SMTP_FROM and SWE_AGENT_DIGEST_TO to email")
        send_email(
            subject=subject,
            body=body,
            sender=settings.smtp_from,
            recipient=settings.digest_to,
            host=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_username,
            password=settings.smtp_password.get_secret_value() if settings.smtp_password else None,
        )
        typer.echo(f"digest emailed to {settings.digest_to}")


@watchdog_app.command()
def cases(
    status: Annotated[CaseStatus, typer.Option(help="Which cases to list.")] = CaseStatus.OPEN,
) -> None:
    """List cases."""
    _, rt = _runtime()
    for case in rt.cases.list_cases(status=status):
        typer.echo(f"#{case.id} {case.verdict.value:9} {case.status.value:9} {case.candidate_repo}")


@watchdog_app.command()
def show(case_id: int) -> None:
    """Show a case's evidence and the notice that would be sent."""
    _, rt = _runtime()
    case = rt.cases.get(case_id)
    if case is None:
        raise _fail(f"no case {case_id}")
    typer.echo(json.dumps(json.loads(case.evidence.model_dump_json()), indent=2))
    title, body = render_issue_notice(case, owner_login=rt.github.authenticated_login())
    typer.echo(f"\n--- notice that would be sent ---\n{title}\n\n{body}")


@watchdog_app.command()
def resolve(
    case_id: int,
    violation: Annotated[bool, typer.Option("--violation", help="Confirm as a violation.")] = False,
    dismiss: Annotated[bool, typer.Option("--dismiss", help="Dismiss the case for good.")] = False,
) -> None:
    """Decide a low-confidence case. Exactly one of --violation or --dismiss."""
    if violation == dismiss:
        raise typer.BadParameter("pass exactly one of --violation or --dismiss")
    _, rt = _runtime()
    try:
        if dismiss:
            rt.cases.dismiss(case_id)
            typer.echo(f"case {case_id} dismissed")
        else:
            rt.cases.upgrade_to_violation(case_id)
            typer.echo(f"case {case_id} confirmed as a violation")
    except (KeyError, ValueError) as exc:
        raise _fail(str(exc)) from exc


@watchdog_app.command()
def notice(
    case_id: int,
    yes: Annotated[bool, typer.Option("--yes", help="Approve without the prompt.")] = False,
    follow_up: Annotated[bool, typer.Option(help="Approve a second notice for this case.")] = False,
) -> None:
    """Send the notice issue for a case, after your approval. Never automatic."""
    _, rt = _runtime()
    login = rt.github.authenticated_login()
    try:
        case = ensure_sendable(rt.cases, case_id, follow_up=follow_up)
        title, body = render_issue_notice(case, owner_login=login)
        if not yes:
            typer.echo(f"Will open an issue on {case.candidate_repo}:\n\n{title}\n\n{body}\n")
            if not typer.confirm("Send this notice?"):
                raise _fail("not sent: approval declined")
        sent = send_issue_notice(
            rt.github, rt.cases, case_id, approved_by=login, confirmed=True, follow_up=follow_up
        )
    except NoticeBlocked as exc:
        raise _fail(str(exc)) from exc
    except GitHubError as exc:
        raise _fail(f"GitHub refused the notice, nothing was recorded: {exc}") from exc
    typer.echo(f"sent: {sent.result_url}")


@watchdog_app.command()
def dmca(
    case_id: int,
    out: Annotated[Path | None, typer.Option(help="Write the draft here instead of stdout.")] = None,
) -> None:
    """Draft a DMCA takedown for you to review and submit yourself. Nothing is sent."""
    _, rt = _runtime()
    case = rt.cases.get(case_id)
    if case is None:
        raise _fail(f"no case {case_id}")
    text = render_dmca_draft(case, owner_login=rt.github.authenticated_login())
    if out is None:
        typer.echo(text)
    else:
        out.write_text(text)
        typer.echo(f"draft written to {out}")
