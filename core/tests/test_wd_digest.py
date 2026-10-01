from email.message import EmailMessage

from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.digest import render_digest, send_email
from swe_agent.watchdog.models import Evidence, Source, Verdict


def ev(repo: str) -> Evidence:
    return Evidence(
        original_repo="jane/app",
        candidate_repo=repo,
        candidate_url=f"https://github.com/{repo}",
        source=Source.FORK,
        checked_at="2026-10-01T00:00:00+00:00",
        original_copyright=["Copyright (c) 2024 Jane Doe"],
        reason="derived from the original and the required attribution is missing",
    )


def test_digest_lists_violations_with_draft_text_and_review_items_without() -> None:
    cases = CaseStore(":memory:")
    v = cases.record("jane/app", "mallory/app", Verdict.VIOLATION, "t", ev("mallory/app"))
    r = cases.record("jane/app", "maybe/app", Verdict.REVIEW, "t", ev("maybe/app"))

    subject, body = render_digest(cases.list_cases(), owner_login="jane")

    assert "1 violation" in subject and "1 to review" in subject
    assert f"agent watchdog notice {v.id}" in body
    assert f"agent watchdog resolve {r.id}" in body
    assert "https://github.com/mallory/app" in body
    assert "Draft issue" in body
    assert body.count("Draft issue") == 1  # review items get no ready-to-send text
    assert "nothing is sent" in body.lower()


def test_empty_digest_says_so() -> None:
    subject, body = render_digest([], owner_login="jane")

    assert "no new" in subject.lower()
    assert "nothing to review" in body.lower()


class FakeSMTP:
    instances: list["FakeSMTP"] = []

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port
        self.tls = False
        self.login_args: tuple[str, str] | None = None
        self.sent: list[EmailMessage] = []
        FakeSMTP.instances.append(self)

    def __enter__(self) -> "FakeSMTP":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def starttls(self) -> None:
        self.tls = True

    def login(self, user: str, password: str) -> None:
        self.login_args = (user, password)

    def send_message(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_send_email_uses_tls_and_login() -> None:
    FakeSMTP.instances.clear()

    send_email(
        subject="S",
        body="B",
        sender="bot@example.com",
        recipient="jane@example.com",
        host="smtp.example.com",
        port=587,
        username="u",
        password="p",
        smtp_factory=FakeSMTP,
    )

    [smtp] = FakeSMTP.instances
    assert (smtp.host, smtp.port, smtp.tls, smtp.login_args) == ("smtp.example.com", 587, True, ("u", "p"))
    msg = smtp.sent[0]
    assert msg["Subject"] == "S" and msg["To"] == "jane@example.com" and msg["From"] == "bot@example.com"
    assert msg.get_content().strip() == "B"
