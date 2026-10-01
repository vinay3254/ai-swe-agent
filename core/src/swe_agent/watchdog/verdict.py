from swe_agent.jev_gate.decision import Decision, Uncertain, gate
from swe_agent.watchdog.license_check import LicenseCheck
from swe_agent.watchdog.models import Source, Verdict, VerdictResult


def decide(
    *,
    source: Source,
    license: LicenseCheck,
    derived: Decision[bool] | None,
    attribution: Decision[bool] | None,
    min_confidence: float,
) -> VerdictResult:
    """A violation needs a copy that is derived AND missing the attribution its license requires.

    Anything uncertain becomes REVIEW, never VIOLATION: a wrong notice harms a third party.
    """
    if not license.checkable:
        return VerdictResult(
            verdict=Verdict.REVIEW, reason="the original names no copyright holder to look for"
        )
    if license.preserved:
        return VerdictResult(verdict=Verdict.CLEAR, reason="the original copyright holder is still named")

    if source is not Source.FORK:  # a GitHub fork is derived by definition
        if derived is None:
            return VerdictResult(verdict=Verdict.REVIEW, reason="derivation was not assessed")
        sure = gate(derived, min_confidence)
        if isinstance(sure, Uncertain):
            return VerdictResult(
                verdict=Verdict.REVIEW,
                reason=f"unsure the repo is derived from the original (confidence {sure.confidence:.2f})",
            )
        if not sure.value:
            return VerdictResult(verdict=Verdict.CLEAR, reason="not derived from the original")

    if license.has_notice_text:
        # The candidate has a notice but not the exact holder: it may be reworded.
        if attribution is None:
            return VerdictResult(
                verdict=Verdict.REVIEW, reason="notice text differs and attribution was not assessed"
            )
        said = gate(attribution, min_confidence)
        if isinstance(said, Uncertain):
            return VerdictResult(
                verdict=Verdict.REVIEW,
                reason=f"unsure whether the reworded notice credits the author (confidence {said.confidence:.2f})",
            )
        if said.value:
            return VerdictResult(verdict=Verdict.CLEAR, reason="the reworded notice still credits the author")

    return VerdictResult(
        verdict=Verdict.VIOLATION,
        reason="derived from the original and the required attribution is missing",
    )
