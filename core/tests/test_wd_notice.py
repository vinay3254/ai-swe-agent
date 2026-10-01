import pytest
from wd_fakes import FakeWatchGitHub, boom

from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.fingerprint import FileMatch
from swe_agent.watchdog.license_check import LicenseCheck
from swe_agent.watchdog.models import Case, CaseStatus, Evidence, Source, Verdict
from swe_agent.watchdog.notice import (
    NoticeBlocked,
    render_dmca_draft,
    render_issue_notice,
    send_issue_notice,
)


def evidence() -> Evidence:
    return Evidence(
        original_repo="jane/app",
        candidate_repo="mallory/app",
        candidate_url="https://github.com/mallory/app",
        source=Source.FORK,
        matched_files=[FileMatch(path="src/core.py", kind="hash", original_path="src/core.py")],
        head_sha="abc123",
        checked_at="2026-10-01T00:00:00+00:00",
        license_check=LicenseCheck(
            checkable=True,
            preserved=False,
            missing=["Copyright (c) 2024 Jane Doe"],
            has_notice_text=False,
            files_checked=["README.md"],
        ),
        original_copyright=["Copyright (c) 2024 Jane Doe"],
        derived=True,
        derived_confidence=0.97,
        reason="derived from the original and the required attribution is missing",
    )


@pytest.fixture
def cases() -> CaseStore:
    return CaseStore(":memory:")


def violation(cases: CaseStore) -> Case:
    return cases.record("jane/app", "mallory/app", Verdict.VIOLATION, "t", evidence())


def test_issue_notice_states_facts_and_a_fix_and_no_confidence_scores(cases: CaseStore) -> None:
    title, body = render_issue_notice(violation(cases), owner_login="jane")

    assert "jane/app" in title
    assert "https://github.com/jane/app" in body
    assert "`src/core.py`" in body
    assert "Copyright (c) 2024 Jane Doe" in body
    assert "README.md" in body
    assert "@jane" in body
    assert "include the original license" in body.lower()
    assert "0.97" not in body and "confidence" not in body.lower()
    assert "@" not in body.replace("@jane", "")  # no other handles or email addresses


def test_dmca_draft_is_a_draft_for_the_owner_to_review_and_submit(cases: CaseStore) -> None:
    text = render_dmca_draft(violation(cases), owner_login="jane")

    assert text.startswith("DRAFT")
    assert "penalty of perjury" in text
    assert "https://github.com/mallory/app" in text
    assert "must be reviewed" in text.lower()


def test_send_creates_the_issue_and_logs_the_audit_record(cases: CaseStore) -> None:
    gh = FakeWatchGitHub()
    case = violation(cases)

    notice = send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)

    assert gh.issues_created[0][0] == "mallory/app"
    assert notice.result_url == "https://github.com/mallory/app/issues/1"
    assert notice.approved_by == "jane" and "Copyright (c) 2024 Jane Doe" in notice.body
    refreshed = cases.get(case.id)
    assert refreshed is not None and refreshed.status is CaseStatus.NOTIFIED


def test_nothing_is_sent_without_explicit_confirmation(cases: CaseStore) -> None:
    gh = FakeWatchGitHub()
    case = violation(cases)

    with pytest.raises(NoticeBlocked, match="approval"):
        send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=False)

    assert gh.issues_created == []


def test_review_cases_cannot_be_sent_until_the_owner_upgrades_them(cases: CaseStore) -> None:
    gh = FakeWatchGitHub()
    case = cases.record("jane/app", "maybe/app", Verdict.REVIEW, "t", evidence())

    with pytest.raises(NoticeBlocked, match="review"):
        send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)

    cases.upgrade_to_violation(case.id)
    send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)
    assert len(gh.issues_created) == 1


def test_one_notice_per_case_unless_the_owner_approves_a_follow_up(cases: CaseStore) -> None:
    gh = FakeWatchGitHub()
    case = violation(cases)
    send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)

    with pytest.raises(NoticeBlocked, match="already"):
        send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)
    assert len(gh.issues_created) == 1

    send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True, follow_up=True)
    assert len(gh.issues_created) == 2
    assert len(cases.notices_for(case.id)) == 2


def test_dismissed_and_clear_cases_cannot_be_sent(cases: CaseStore) -> None:
    gh = FakeWatchGitHub()
    case = violation(cases)
    cases.dismiss(case.id)

    with pytest.raises(NoticeBlocked, match="dismissed"):
        send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)
    assert gh.issues_created == []


def test_unknown_case_is_blocked(cases: CaseStore) -> None:
    with pytest.raises(NoticeBlocked, match="no case"):
        send_issue_notice(FakeWatchGitHub(), cases, 404, approved_by="jane", confirmed=True)


def test_failed_send_leaves_the_case_open_and_logs_nothing(cases: CaseStore) -> None:
    gh = FakeWatchGitHub(errors={"mallory/app": boom(410)})
    case = violation(cases)

    with pytest.raises(Exception, match="GitHub exploded"):
        send_issue_notice(gh, cases, case.id, approved_by="jane", confirmed=True)

    refreshed = cases.get(case.id)
    assert refreshed is not None and refreshed.status is CaseStatus.OPEN
    assert cases.notices_for(case.id) == []
