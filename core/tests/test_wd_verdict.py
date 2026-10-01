import pytest

from swe_agent.jev_gate.decision import Decision, noul_decision
from swe_agent.watchdog.license_check import LicenseCheck
from swe_agent.watchdog.models import Source, Verdict
from swe_agent.watchdog.verdict import decide

MIN = 0.85


def lic(preserved: bool = False, has_notice: bool = True, checkable: bool = True) -> LicenseCheck:
    return LicenseCheck(
        checkable=checkable,
        preserved=preserved,
        missing=[] if preserved else ["Copyright (c) 2024 Jane Doe"],
        has_notice_text=has_notice,
        files_checked=["LICENSE"],
    )


def yes(p: float = 0.99) -> Decision[bool]:
    return noul_decision(p)


def no(p: float = 0.01) -> Decision[bool]:
    return noul_decision(p)


def verdict(**kw: object) -> Verdict:
    args: dict[str, object] = {
        "source": Source.CODE_SEARCH,
        "license": lic(),
        "derived": yes(),
        "attribution": None,
        "min_confidence": MIN,
    }
    args.update(kw)
    return decide(**args).verdict  # pyright: ignore[reportArgumentType]


def test_preserved_attribution_is_clear_without_asking_anyone() -> None:
    assert verdict(license=lic(preserved=True), derived=None) is Verdict.CLEAR


def test_fork_with_no_notice_files_is_a_violation_without_any_jev_answer() -> None:
    assert verdict(source=Source.FORK, license=lic(has_notice=False), derived=None) is Verdict.VIOLATION


def test_confidently_derived_copy_with_no_notice_is_a_violation() -> None:
    assert verdict(license=lic(has_notice=False)) is Verdict.VIOLATION


def test_not_derived_is_clear_even_if_no_attribution() -> None:
    assert verdict(license=lic(has_notice=False), derived=no()) is Verdict.CLEAR


def test_unsure_derivation_goes_to_human_review() -> None:
    assert verdict(license=lic(has_notice=False), derived=yes(0.7)) is Verdict.REVIEW


def test_missing_derivation_answer_goes_to_human_review() -> None:
    assert verdict(license=lic(has_notice=False), derived=None) is Verdict.REVIEW


def test_notice_text_without_exact_holder_needs_jev_to_confirm_missing_attribution() -> None:
    assert verdict(attribution=no()) is Verdict.VIOLATION
    assert verdict(attribution=yes()) is Verdict.CLEAR


def test_unsure_attribution_goes_to_human_review() -> None:
    assert verdict(attribution=yes(0.6)) is Verdict.REVIEW


def test_notice_text_without_a_jev_attribution_answer_goes_to_review() -> None:
    assert verdict(attribution=None) is Verdict.REVIEW


def test_uncheckable_original_goes_to_human_review() -> None:
    assert verdict(license=lic(checkable=False)) is Verdict.REVIEW


@pytest.mark.parametrize("source", [Source.FORK, Source.CODE_SEARCH])
def test_violation_reason_is_human_readable(source: Source) -> None:
    result = decide(
        source=source,
        license=lic(has_notice=False),
        derived=yes(),
        attribution=None,
        min_confidence=MIN,
    )

    assert result.verdict is Verdict.VIOLATION
    assert "attribution" in result.reason
