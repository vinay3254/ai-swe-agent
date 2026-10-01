import smtplib
from collections.abc import Callable
from email.message import EmailMessage
from typing import Any

from swe_agent.watchdog.models import Case, CaseStatus, Verdict
from swe_agent.watchdog.notice import render_issue_notice


def render_digest(cases: list[Case], *, owner_login: str) -> tuple[str, str]:
    """Plain-text digest of open cases. Only violations carry ready-to-send issue text."""
    open_cases = [c for c in cases if c.status is CaseStatus.OPEN]
    violations = [c for c in open_cases if c.verdict is Verdict.VIOLATION]
    reviews = [c for c in open_cases if c.verdict is Verdict.REVIEW]
    if not open_cases:
        return "Watchdog: no new cases", "Nothing to review.\n"

    out: list[str] = []
    for case in violations:
        title, body = render_issue_notice(case, owner_login=owner_login)
        ev = case.evidence
        out.append(
            f"VIOLATION #{case.id}: {ev.candidate_url}\n"
            f"  Why: {ev.reason}\n"
            f"  Matched files: {', '.join(m.path for m in ev.matched_files) or 'none individually'}\n"
            f"  Checked at: {ev.checked_at} (commit {ev.head_sha or 'unknown'})\n"
            f"  To send this notice: agent watchdog notice {case.id}\n\n"
            f"  Draft issue: {title}\n" + "\n".join(f"  | {line}" for line in body.splitlines()) + "\n"
        )
    for case in reviews:
        ev = case.evidence
        out.append(
            f"REVIEW #{case.id}: {ev.candidate_url}\n"
            f"  Why it needs you: {ev.reason}\n"
            f"  Decide: agent watchdog resolve {case.id} --violation   or   --dismiss\n"
        )
    footer = "Nothing is sent until you approve it with the command shown on each item.\n"
    subject = f"Watchdog: {len(violations)} violation(s), {len(reviews)} to review"
    return subject, "\n".join(out) + "\n" + footer


def send_email(
    *,
    subject: str,
    body: str,
    sender: str,
    recipient: str,
    host: str,
    port: int,
    username: str | None,
    password: str | None,
    smtp_factory: Callable[[str, int], Any] = smtplib.SMTP,
) -> None:
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = recipient
    message.set_content(body)
    with smtp_factory(host, port) as smtp:
        smtp.starttls()
        if username is not None and password is not None:
            smtp.login(username, password)
        smtp.send_message(message)
