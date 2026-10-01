import pytest
from wd_fakes import CORE, MIT, ORIGINAL_FILES, FakeWatchGate, FakeWatchGitHub, boom, fork, hit

from swe_agent.config import Thresholds
from swe_agent.watchdog.cases import CaseStore
from swe_agent.watchdog.models import CaseStatus, Source, Verdict
from swe_agent.watchdog.scan import Watchdog

ORIGINAL = "jane/app"
STRIPPED = {"src/core.py": CORE, "README.md": "# my cool fork\n"}  # LICENSE removed
KEPT = {"src/core.py": CORE, "LICENSE": MIT}


@pytest.fixture
def cases() -> CaseStore:
    return CaseStore(":memory:")


def make(gh: FakeWatchGitHub, cases: CaseStore, gate: FakeWatchGate | None = None) -> Watchdog:
    return Watchdog(github=gh, gate=gate or FakeWatchGate(), cases=cases, thresholds=Thresholds())


def github(**kw: object) -> FakeWatchGitHub:
    gh = FakeWatchGitHub(repos={ORIGINAL: ORIGINAL_FILES})
    for k, v in kw.items():
        setattr(gh, k, v)
    return gh


def test_fork_that_stripped_the_license_is_a_violation_with_evidence(cases: CaseStore) -> None:
    gh = github(forks=[fork("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED

    report = make(gh, cases).scan(ORIGINAL)

    [case] = report.violations
    assert case.verdict is Verdict.VIOLATION and case.status is CaseStatus.OPEN
    ev = case.evidence
    assert ev.candidate_url == "https://github.com/mallory/app"
    assert ev.source is Source.FORK
    assert [(m.path, m.kind) for m in ev.matched_files] == [("src/core.py", "hash")]
    assert ev.original_copyright == ["Copyright (c) 2024 Jane Doe"]
    assert ev.license_check is not None and not ev.license_check.preserved
    assert ev.head_sha == "sha-mallory/app"


def test_plain_fork_that_keeps_the_license_is_not_a_violation(cases: CaseStore) -> None:
    gh = github(forks=[fork("fan/app")])
    gh.repos["fan/app"] = KEPT

    report = make(gh, cases).scan(ORIGINAL)

    assert report.violations == [] and report.reviews == []
    [case] = cases.list_cases()
    assert case.verdict is Verdict.CLEAR and case.status is CaseStatus.CLOSED


def test_collaborator_forks_are_skipped(cases: CaseStore) -> None:
    gh = github(forks=[fork("jane/app-experiments"), fork("helper/app")], collaborators={"jane", "helper"})
    gh.repos["helper/app"] = STRIPPED

    report = make(gh, cases).scan(ORIGINAL)

    assert report.candidates_seen == 0
    assert cases.list_cases() == []


def test_unavailable_collaborator_list_trusts_only_the_owner_and_says_so(cases: CaseStore) -> None:
    gh = github(forks=[fork("jane/other"), fork("mallory/app")], collaborators=None)
    gh.repos["mallory/app"] = STRIPPED

    report = make(gh, cases).scan(ORIGINAL)

    assert [c.candidate_repo for c in report.violations] == ["mallory/app"]
    assert any("collaborator" in n for n in report.notes)


def test_unchanged_candidates_are_skipped_on_the_next_scan(cases: CaseStore) -> None:
    gh = github(forks=[fork("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED
    watchdog = make(gh, cases)

    watchdog.scan(ORIGINAL)
    second = watchdog.scan(ORIGINAL)

    assert second.skipped_unchanged == 1 and second.violations == []

    gh.forks = [fork("mallory/app", pushed_at="2026-10-01T00:00:00Z")]
    third = watchdog.scan(ORIGINAL)
    assert len(third.violations) == 1


def test_code_search_copy_with_no_notice_is_a_violation_when_jev_is_sure(cases: CaseStore) -> None:
    gh = github(search_hits=[hit("copycat/lib")])
    gh.repos["copycat/lib"] = {"src/core.py": CORE.replace("sha256", "sha256") + "# tweak\n"}
    gate = FakeWatchGate(derived_p=0.99)

    report = make(gh, cases, gate).scan(ORIGINAL)

    assert [c.candidate_repo for c in report.violations] == ["copycat/lib"]
    assert report.violations[0].evidence.source is Source.CODE_SEARCH
    assert gate.attribution_calls == 0  # no notice text, so no reworded text to judge


def test_unsure_jev_derivation_goes_to_the_review_queue_not_violation(cases: CaseStore) -> None:
    gh = github(search_hits=[hit("copycat/lib")])
    partial = (
        "def compute_distinctive_checksum(payload, salt):\n    pass\n"
        "def another_unusually_long_function_name_for_fingerprints(value): return value * 31\n"
    )
    gh.repos["copycat/lib"] = {"src/core.py": partial}

    report = make(gh, cases, FakeWatchGate(derived_p=0.7)).scan(ORIGINAL)

    assert report.violations == []
    assert [c.candidate_repo for c in report.reviews] == ["copycat/lib"]


def test_reworded_notice_is_judged_by_jev(cases: CaseStore) -> None:
    gh = github(forks=[fork("reword/app")])
    gh.repos["reword/app"] = {"src/core.py": CORE, "LICENSE": "MIT. Original work by J. Doe, 2024.\n"}
    gate = FakeWatchGate(attributed_p=0.97)

    report = make(gh, cases, gate).scan(ORIGINAL)

    assert gate.attribution_calls == 1
    assert report.violations == [] and report.reviews == []


def test_search_hit_without_file_level_match_is_incidental_and_clear(cases: CaseStore) -> None:
    gh = github(search_hits=[hit("other/thing")])
    gh.repos["other/thing"] = {"main.py": "print('hello')\n"}
    gate = FakeWatchGate()

    report = make(gh, cases, gate).scan(ORIGINAL)

    assert report.violations == [] and report.reviews == []
    assert gate.derivation_calls == 0


def test_fork_found_by_search_is_assessed_once(cases: CaseStore) -> None:
    gh = github(forks=[fork("mallory/app")], search_hits=[hit("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED

    report = make(gh, cases).scan(ORIGINAL)

    assert report.candidates_seen == 1
    assert len(cases.list_cases()) == 1


def test_original_without_a_copyright_line_cannot_be_protected(cases: CaseStore) -> None:
    gh = FakeWatchGitHub(repos={ORIGINAL: {"src/core.py": CORE, "LICENSE": "Public domain.\n"}}, forks=[fork("x/app")])

    report = make(gh, cases).scan(ORIGINAL)

    assert report.candidates_seen == 0
    assert any("copyright" in n.lower() for n in report.notes)


def test_one_failing_candidate_does_not_abort_the_scan(cases: CaseStore) -> None:
    gh = github(forks=[fork("broken/app"), fork("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED
    gh.errors["broken/app"] = boom()

    report = make(gh, cases).scan(ORIGINAL)

    assert [c.candidate_repo for c in report.violations] == ["mallory/app"]
    assert len(report.errors) == 1 and "broken/app" in report.errors[0]
    assert cases.seen_pushed_at(ORIGINAL, "broken/app") is None  # retried next scan


def test_search_failure_is_reported_and_forks_are_still_checked(cases: CaseStore) -> None:
    class SearchDown(FakeWatchGitHub):
        def search_code(self, line: str, *, exclude_owner: str):  # type: ignore[no-untyped-def]
            raise boom(403)

    gh = SearchDown(repos={ORIGINAL: ORIGINAL_FILES, "mallory/app": STRIPPED}, forks=[fork("mallory/app")])

    report = make(gh, cases).scan(ORIGINAL)

    assert len(report.violations) == 1
    assert any("code search" in e for e in report.errors)


def test_a_dismissed_case_is_not_reopened(cases: CaseStore) -> None:
    gh = github(forks=[fork("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED
    watchdog = make(gh, cases)
    watchdog.scan(ORIGINAL)
    [case] = cases.list_cases()
    cases.dismiss(case.id)
    gh.forks = [fork("mallory/app", pushed_at="2026-12-01T00:00:00Z")]

    report = make(gh, cases).scan(ORIGINAL)

    assert report.violations == []
    [again] = cases.list_cases()
    assert again.status is CaseStatus.DISMISSED


def test_scan_never_writes_to_github(cases: CaseStore) -> None:
    gh = github(forks=[fork("mallory/app")])
    gh.repos["mallory/app"] = STRIPPED

    make(gh, cases).scan(ORIGINAL)

    assert gh.issues_created == []


def test_report_states_what_the_watchdog_cannot_see(cases: CaseStore) -> None:
    report = make(github(), cases).scan(ORIGINAL)

    assert any("private" in n for n in report.notes)
