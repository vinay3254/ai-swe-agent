from typing import Protocol

from swe_agent.watchdog.cases import CaseStore, Notice
from swe_agent.watchdog.models import Case, CaseStatus, Verdict


class NoticeBlocked(Exception):
    """A safeguard stopped the send. Nothing was sent."""


class IssueCreator(Protocol):
    def create_issue(self, repo: str, *, title: str, body: str) -> str: ...


def _facts(case: Case) -> str:
    ev = case.evidence
    files = ", ".join(f"`{m.path}`" for m in ev.matched_files) or "none identified individually"
    lines = "\n".join(f"  - `{line}`" for line in ev.original_copyright)
    checked = ", ".join(f"`{f}`" for f in (ev.license_check.files_checked if ev.license_check else [])) or "none"
    return (
        f"- Files that match the original: {files}\n"
        f"- Copyright notice the original license requires you to keep:\n{lines}\n"
        f"- Files we looked in for that notice: {checked}"
    )


def render_issue_notice(case: Case, *, owner_login: str) -> tuple[str, str]:
    """A fixed, factual template. It states what was found and how to fix it, nothing else."""
    ev = case.evidence
    original = f"https://github.com/{ev.original_repo}"
    title = f"License attribution missing from the copy of {ev.original_repo}"
    body = (
        f"Hello,\n\n"
        f"This repository appears to include code from [{ev.original_repo}]({original}). "
        f"That project's license requires its copyright notice to be kept with copies of the code.\n\n"
        f"What we found:\n{_facts(case)}\n\n"
        f"How to fix it: include the original license text and the copyright notice above in this "
        f"repository, for example in a `LICENSE` file.\n\n"
        f"If this is a mistake (for example the notice is kept somewhere we did not look, or you have "
        f"the author's permission), reply here and we will close this issue.\n\n"
        f"This notice was reviewed and sent by @{owner_login}. It is not a legal claim."
    )
    return title, body


def render_dmca_draft(case: Case, *, owner_login: str) -> str:
    """Text for the owner to review, adapt and submit themselves. The tool never submits it."""
    ev = case.evidence
    return (
        "DRAFT - not sent. This must be reviewed and submitted by the copyright owner "
        f"({owner_login}) through GitHub's DMCA form. It includes a statement under penalty of perjury.\n\n"
        f"Original work: https://github.com/{ev.original_repo}\n"
        f"Allegedly infringing material: {ev.candidate_url}\n"
        f"Files: {', '.join(m.path for m in ev.matched_files) or 'see repository'}\n"
        f"Basis: {ev.reason}.\n"
        f"Copyright notice that was not preserved:\n"
        + "\n".join(f"  {line}" for line in ev.original_copyright)
        + "\n\n"
        "I have a good faith belief that use of the material in the manner complained of is not "
        "authorized by the copyright owner, its agent, or the law.\n"
        "I swear, under penalty of perjury, that the information in this notification is accurate "
        "and that I am the copyright owner or authorized to act on the owner's behalf.\n\n"
        f"Signed: {owner_login}\n"
    )


def ensure_sendable(cases: CaseStore, case_id: int, *, follow_up: bool = False) -> Case:
    """Return the case if a notice may go out for it now, else raise NoticeBlocked."""
    case = cases.get(case_id)
    if case is None:
        raise NoticeBlocked(f"no case {case_id}")
    if case.status is CaseStatus.DISMISSED:
        raise NoticeBlocked(f"case {case_id} was dismissed")
    if case.status is CaseStatus.CLOSED:
        raise NoticeBlocked(f"case {case_id} was assessed clear")
    if case.status is CaseStatus.NOTIFIED and not follow_up:
        raise NoticeBlocked(f"case {case_id} was already sent a notice; a follow-up needs new approval")
    if case.verdict is not Verdict.VIOLATION:
        raise NoticeBlocked(
            f"case {case_id} is in the review queue; confirm it as a violation or dismiss it first"
        )
    return case


def send_issue_notice(
    github: IssueCreator,
    cases: CaseStore,
    case_id: int,
    *,
    approved_by: str,
    confirmed: bool,
    follow_up: bool = False,
) -> Notice:
    """Open an issue on the candidate repo. There is no auto-send: `confirmed` must be True."""
    if not confirmed:
        raise NoticeBlocked("the owner's approval is required before anything is sent")
    case = ensure_sendable(cases, case_id, follow_up=follow_up)
    title, body = render_issue_notice(case, owner_login=approved_by)
    url = github.create_issue(case.candidate_repo, title=title, body=body)
    return cases.record_notice(
        case_id, kind="github_issue", title=title, body=body, approved_by=approved_by, result_url=url
    )
