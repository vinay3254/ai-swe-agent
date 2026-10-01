import pytest

from swe_agent.config import Thresholds
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import IssueKind, Review, Triage
from swe_agent.outcome import Outcome, decide_outcome, triage_blocker

T = Thresholds()


def triage(
    kind: IssueKind = IssueKind.BUG,
    kind_conf: float = 0.9,
    complexity: float = 1.0,
    complexity_conf: float = 0.9,
    fixable: bool = True,
    fixable_conf: float = 0.9,
) -> Triage:
    return Triage(
        kind=Decision(value=kind, confidence=kind_conf, probabilities={}),
        complexity=Decision(value=complexity, confidence=complexity_conf, probabilities={}),
        fixable=Decision(value=fixable, confidence=fixable_conf, probabilities={}),
    )


def review(
    addresses: bool = True, a_conf: float = 0.9, risk: float = 0.2, r_conf: float = 0.9
) -> Review:
    return Review(
        addresses_issue=Decision(value=addresses, confidence=a_conf, probabilities={}),
        risk=Decision(value=risk, confidence=r_conf, probabilities={}),
    )


def test_triage_allows_a_confident_small_bug() -> None:
    assert triage_blocker(triage(), T) is None


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({"kind_conf": 0.5}, "unsure what kind"),
        ({"complexity_conf": 0.5}, "unsure how complex"),
        ({"fixable_conf": 0.5}, "unsure the agent can fix"),
        ({"kind": IssueKind.QUESTION}, "not a code change"),
        ({"kind": IssueKind.UNCLEAR}, "not a code change"),
        ({"fixable": False}, "not fixable"),
        ({"complexity": 3.0}, "above the limit"),
    ],
)
def test_triage_blockers(kwargs: dict[str, object], expected: str) -> None:
    reason = triage_blocker(triage(**kwargs), T)  # pyright: ignore[reportArgumentType]
    assert reason is not None and expected in reason


def test_triage_allows_complexity_exactly_at_limit() -> None:
    assert triage_blocker(triage(complexity=T.max_complexity), T) is None


def test_ready_pr_when_everything_is_good() -> None:
    v = decide_outcome(review=review(), tests_passed=True, incomplete=False, thresholds=T)
    assert v.outcome is Outcome.READY_PR


def test_escalate_when_review_is_unsure_the_diff_addresses_the_issue() -> None:
    v = decide_outcome(
        review=review(a_conf=0.2), tests_passed=True, incomplete=False, thresholds=T
    )
    assert v.outcome is Outcome.ESCALATE


def test_escalate_when_review_says_diff_does_not_address_issue() -> None:
    v = decide_outcome(
        review=review(addresses=False), tests_passed=True, incomplete=False, thresholds=T
    )
    assert v.outcome is Outcome.ESCALATE


def test_draft_when_tests_fail() -> None:
    v = decide_outcome(review=review(), tests_passed=False, incomplete=False, thresholds=T)
    assert v.outcome is Outcome.DRAFT_PR and "tests" in v.reason


def test_draft_when_agent_is_incomplete_even_if_tests_pass() -> None:
    v = decide_outcome(review=review(), tests_passed=True, incomplete=True, thresholds=T)
    assert v.outcome is Outcome.DRAFT_PR and "budget" in v.reason


def test_draft_when_risk_is_high() -> None:
    v = decide_outcome(
        review=review(risk=1.5), tests_passed=True, incomplete=False, thresholds=T
    )
    assert v.outcome is Outcome.DRAFT_PR and "risk" in v.reason


def test_draft_when_risk_confidence_is_low() -> None:
    v = decide_outcome(
        review=review(r_conf=0.1), tests_passed=True, incomplete=False, thresholds=T
    )
    assert v.outcome is Outcome.DRAFT_PR


def test_failing_tests_never_beat_an_unsure_review() -> None:
    v = decide_outcome(
        review=review(a_conf=0.1), tests_passed=False, incomplete=True, thresholds=T
    )
    assert v.outcome is Outcome.ESCALATE
