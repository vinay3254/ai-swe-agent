from dataclasses import dataclass
from enum import StrEnum

from swe_agent.config import Thresholds
from swe_agent.jev_gate.decision import Uncertain, gate
from swe_agent.jev_gate.questions import IssueKind, Review, Triage


class Outcome(StrEnum):
    READY_PR = "ready_pr"
    DRAFT_PR = "draft_pr"
    ESCALATE = "escalate"


@dataclass(frozen=True)
class Verdict:
    outcome: Outcome
    reason: str


def triage_blocker(triage: Triage, t: Thresholds) -> str | None:
    """Return why the agent must not run, or None when it may proceed."""
    kind = gate(triage.kind, t.triage_min_confidence)
    if isinstance(kind, Uncertain):
        return f"triage is unsure what kind of issue this is (confidence {kind.confidence:.2f})"
    complexity = gate(triage.complexity, t.triage_min_confidence)
    if isinstance(complexity, Uncertain):
        return f"triage is unsure how complex this is (confidence {complexity.confidence:.2f})"
    fixable = gate(triage.fixable, t.triage_min_confidence)
    if isinstance(fixable, Uncertain):
        return f"triage is unsure the agent can fix this (confidence {fixable.confidence:.2f})"
    if kind.value in (IssueKind.QUESTION, IssueKind.UNCLEAR):
        return f"issue is a {kind.value}, not a code change"
    if not fixable.value:
        return "triage judged this not fixable by an agent"
    if complexity.value > t.max_complexity:
        return f"complexity {complexity.value:.1f} is above the limit {t.max_complexity:.1f}"
    return None


def decide_outcome(
    *, review: Review, tests_passed: bool, incomplete: bool, thresholds: Thresholds
) -> Verdict:
    """Choose ready PR, draft PR, or escalation. Tests and budget are deterministic inputs."""
    addresses = gate(review.addresses_issue, thresholds.review_min_confidence)
    if isinstance(addresses, Uncertain):
        return Verdict(
            Outcome.ESCALATE,
            "review gate is unsure the diff addresses the issue "
            f"(confidence {addresses.confidence:.2f})",
        )
    if not addresses.value:
        return Verdict(Outcome.ESCALATE, "review gate says the diff does not address the issue")
    if incomplete:
        return Verdict(Outcome.DRAFT_PR, "agent hit its step or time budget; work is partial")
    if not tests_passed:
        return Verdict(Outcome.DRAFT_PR, "tests are failing")
    risk = gate(review.risk, thresholds.review_min_confidence)
    if isinstance(risk, Uncertain):
        return Verdict(
            Outcome.DRAFT_PR, f"risk estimate has low confidence ({risk.confidence:.2f})"
        )
    if risk.value > thresholds.max_ready_risk:
        return Verdict(
            Outcome.DRAFT_PR,
            f"risk {risk.value:.1f} is above the limit {thresholds.max_ready_risk:.1f}",
        )
    return Verdict(Outcome.READY_PR, "tests pass and the review gate approves")
