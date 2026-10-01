from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.models import CaseStatus, Evidence, Source, Verdict


def evidence(reason: str = "r", repo: str = "evil/copy") -> Evidence:
    return Evidence(
        original_repo="octo/app",
        candidate_repo=repo,
        candidate_url=f"https://github.com/{repo}",
        source=Source.FORK,
        checked_at="2026-10-01T00:00:00+00:00",
        reason=reason,
    )


@pytest.fixture
def cases() -> CaseStore:
    return CaseStore(":memory:")


def test_violation_and_review_cases_are_open_and_clear_cases_are_closed(cases: CaseStore) -> None:
    v = cases.record("octo/app", "a/a", Verdict.VIOLATION, "t1", evidence(repo="a/a"))
    r = cases.record("octo/app", "b/b", Verdict.REVIEW, "t1", evidence(repo="b/b"))
    c = cases.record("octo/app", "c/c", Verdict.CLEAR, "t1", evidence(repo="c/c"))

    assert (v.status, r.status, c.status) == (CaseStatus.OPEN, CaseStatus.OPEN, CaseStatus.CLOSED)
    assert [x.candidate_repo for x in cases.list_cases(status=CaseStatus.OPEN)] == ["a/a", "b/b"]


def test_rescan_updates_the_same_case_instead_of_duplicating(cases: CaseStore) -> None:
    first = cases.record("octo/app", "a/a", Verdict.REVIEW, "t1", evidence("old"))
    second = cases.record("octo/app", "a/a", Verdict.VIOLATION, "t2", evidence("new"))

    assert second.id == first.id
    assert second.verdict is Verdict.VIOLATION and second.evidence.reason == "new"
    assert len(cases.list_cases()) == 1


def test_dismissed_and_notified_cases_are_never_reopened_by_a_rescan(cases: CaseStore) -> None:
    case = cases.record("octo/app", "a/a", Verdict.VIOLATION, "t1", evidence("old"))
    cases.dismiss(case.id)

    again = cases.record("octo/app", "a/a", Verdict.VIOLATION, "t2", evidence("new"))

    assert again.status is CaseStatus.DISMISSED
    assert again.evidence.reason == "old"


def test_seen_pushed_at_is_the_scan_cursor(cases: CaseStore) -> None:
    assert cases.seen_pushed_at("octo/app", "a/a") is None
    cases.record("octo/app", "a/a", Verdict.CLEAR, "2026-01-01T00:00:00Z", evidence())
    assert cases.seen_pushed_at("octo/app", "a/a") == "2026-01-01T00:00:00Z"


def test_owner_can_upgrade_a_review_case_to_violation(cases: CaseStore) -> None:
    case = cases.record("octo/app", "a/a", Verdict.REVIEW, "t", evidence())

    upgraded = cases.upgrade_to_violation(case.id)

    assert upgraded.verdict is Verdict.VIOLATION and upgraded.status is CaseStatus.OPEN
    assert any("owner" in n for n in upgraded.evidence.notes)


def test_cannot_upgrade_a_dismissed_or_clear_case(cases: CaseStore) -> None:
    clear = cases.record("octo/app", "c/c", Verdict.CLEAR, "t", evidence(repo="c/c"))
    with pytest.raises(ValueError, match="open"):
        cases.upgrade_to_violation(clear.id)


def test_record_notice_marks_the_case_notified_and_keeps_an_audit_trail(cases: CaseStore) -> None:
    case = cases.record("octo/app", "a/a", Verdict.VIOLATION, "t", evidence())

    notice = cases.record_notice(
        case.id, kind="github_issue", title="T", body="B", approved_by="octo", result_url="https://x/1"
    )

    assert notice.case_id == case.id and notice.body == "B" and notice.approved_by == "octo"
    refreshed = cases.get(case.id)
    assert refreshed is not None and refreshed.status is CaseStatus.NOTIFIED
    assert [n.result_url for n in cases.notices_for(case.id)] == ["https://x/1"]


def test_get_unknown_case_is_none_and_dismiss_unknown_raises(cases: CaseStore) -> None:
    assert cases.get(99) is None
    with pytest.raises(KeyError):
        cases.dismiss(99)


def test_store_is_thread_safe(tmp_path: Path) -> None:
    store = CaseStore(tmp_path / "w.db")

    def work(i: int) -> None:
        store.record("octo/app", f"u/r{i}", Verdict.REVIEW, "t", evidence(repo=f"u/r{i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(work, range(24)))

    assert len(store.list_cases()) == 24
