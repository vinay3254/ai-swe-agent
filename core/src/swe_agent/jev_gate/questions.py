from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel

from swe_agent.jev_gate.client import (
    Asker,
    ChoiceAnswer,
    JevProtocolError,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from swe_agent.jev_gate.decision import Decision, noul_decision
from swe_agent.models import Issue


class IssueKind(StrEnum):
    BUG = "bug"
    FEATURE = "feature"
    QUESTION = "question"
    UNCLEAR = "unclear"


class Triage(BaseModel, frozen=True):
    kind: Decision[IssueKind]
    complexity: Decision[float]  # 0 trivial .. 4 epic
    fixable: Decision[bool]


class Review(BaseModel, frozen=True):
    addresses_issue: Decision[bool]
    risk: Decision[float]  # 0 low .. 2 high


class Gate(Protocol):
    def triage(self, issue: Issue) -> Triage: ...
    def review(self, issue: Issue, diff: str) -> Review: ...


TRIAGE_QUESTIONS = {
    "kind": Question(
        type="choice",
        instructions="What kind of GitHub issue is this?",
        criteria={
            "bug": "Something that should work is broken",
            "feature": "A request for new behavior",
            "question": "Asks for help or information; no code change needed",
            "unclear": "Not enough information to act on",
        },
    ),
    "complexity": Question(
        type="score",
        instructions="How much code must change to resolve this issue?",
        criteria=[
            "Trivial: a one-line or config change",
            "Small: one function",
            "Moderate: several functions in one module",
            "Large: several modules",
            "Epic: an architectural change",
        ],
    ),
    "fixable": Question(
        type="noul",
        instructions=(
            "Can an autonomous coding agent resolve this issue by changing code in "
            "this repository and checking the result with tests?"
        ),
        criteria={
            "true": "Self-contained and verifiable with the repository's tests",
            "false": "Needs a product decision, outside access, or human judgment",
        },
    ),
}

REVIEW_QUESTIONS = {
    "addresses": Question(
        type="noul",
        instructions="Does this diff resolve the issue described in the state?",
        criteria={
            "true": "The diff makes the change the issue asks for",
            "false": "The diff is unrelated, partial, or does something else",
        },
    ),
    "risk": Question(
        type="score",
        instructions="How risky is it to merge this diff?",
        criteria=[
            "Low: small, local, easy to revert",
            "Moderate: touches shared code paths",
            "High: broad, hard to revert, or changes security-relevant behavior",
        ],
    ),
}


def _clip(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n[truncated]"


def _noul(answer: object) -> Decision[bool]:
    if not isinstance(answer, NoulAnswer):
        raise JevProtocolError(f"expected a noul answer, got {type(answer).__name__}")
    return noul_decision(answer.noul)


def _score(answer: object) -> Decision[float]:
    if not isinstance(answer, ScoreAnswer):
        raise JevProtocolError(f"expected a score answer, got {type(answer).__name__}")
    return Decision(
        value=answer.score, confidence=answer.confidence, probabilities=answer.probabilities
    )


def _kind(answer: object) -> Decision[IssueKind]:
    if not isinstance(answer, ChoiceAnswer):
        raise JevProtocolError(f"expected a choice answer, got {type(answer).__name__}")
    try:
        kind = IssueKind(answer.choice)
    except ValueError as exc:
        raise JevProtocolError(f"unknown issue kind {answer.choice!r}") from exc
    return Decision(value=kind, confidence=answer.confidence, probabilities=answer.probabilities)


class JevGate:
    """Builds typed Jev questions for triage and review and parses the answers."""

    def __init__(self, asker: Asker, *, max_issue_chars: int, max_diff_chars: int) -> None:
        self._asker = asker
        self._max_issue_chars = max_issue_chars
        self._max_diff_chars = max_diff_chars

    def _issue_state(self, issue: Issue) -> dict[str, Any]:
        return {
            "title": _clip(issue.title, 500),
            "body": _clip(issue.body, self._max_issue_chars),
            "labels": list(issue.labels),
        }

    def triage(self, issue: Issue) -> Triage:
        answers = self._asker.ask({"issue": self._issue_state(issue)}, TRIAGE_QUESTIONS)
        return Triage(
            kind=_kind(answers["kind"]),
            complexity=_score(answers["complexity"]),
            fixable=_noul(answers["fixable"]),
        )

    def review(self, issue: Issue, diff: str) -> Review:
        state = {"issue": self._issue_state(issue), "diff": _clip(diff, self._max_diff_chars)}
        answers = self._asker.ask(state, REVIEW_QUESTIONS)
        return Review(addresses_issue=_noul(answers["addresses"]), risk=_score(answers["risk"]))
