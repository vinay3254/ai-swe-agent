from collections.abc import Mapping
from typing import Any

import pytest

from swe_agent.jev_gate.client import (
    ChoiceAnswer,
    JevProtocolError,
    NoulAnswer,
    Question,
    ScoreAnswer,
)
from swe_agent.jev_gate.questions import IssueKind, JevGate
from swe_agent.models import Issue, IssueRef

type AnyAnswer = NoulAnswer | ChoiceAnswer | ScoreAnswer

ISSUE = Issue(
    ref=IssueRef(owner="octo", repo="app", number=1),
    title="Crash on empty input",
    body="Calling parse('') raises IndexError.",
    labels=("bug",),
)


class FakeAsker:
    def __init__(self, answers: dict[str, AnyAnswer]) -> None:
        self.answers = answers
        self.states: list[Mapping[str, Any]] = []
        self.questions: list[Mapping[str, Question]] = []

    def ask(
        self, state: Mapping[str, Any], questions: Mapping[str, Question]
    ) -> dict[str, AnyAnswer]:
        self.states.append(state)
        self.questions.append(questions)
        return self.answers


def triage_answers() -> dict[str, AnyAnswer]:
    return {
        "kind": ChoiceAnswer(
            type="choice", choice="bug", probabilities={"bug": 0.9}, confidence=0.85
        ),
        "complexity": ScoreAnswer(
            type="score", score=1.2, legend={"1": "Small"}, probabilities={"1": 1.0}, confidence=0.8
        ),
        "fixable": NoulAnswer(type="noul", noul=0.9),
    }


def review_answers() -> dict[str, AnyAnswer]:
    return {
        "addresses": NoulAnswer(type="noul", noul=0.97),
        "risk": ScoreAnswer(
            type="score", score=0.3, legend={"0": "Low"}, probabilities={"0": 1.0}, confidence=0.9
        ),
    }


def make_gate(asker: FakeAsker, **kw: int) -> JevGate:
    return JevGate(
        asker,
        max_issue_chars=kw.get("max_issue_chars", 1000),
        max_diff_chars=kw.get("max_diff_chars", 1000),
    )


def test_triage_maps_answers_to_decisions() -> None:
    asker = FakeAsker(triage_answers())

    triage = make_gate(asker).triage(ISSUE)

    assert triage.kind.value is IssueKind.BUG
    assert triage.kind.confidence == 0.85
    assert triage.complexity.value == 1.2
    assert triage.fixable.value is True
    assert triage.fixable.confidence == pytest.approx(0.8)
    assert set(asker.questions[0]) == {"kind", "complexity", "fixable"}
    assert asker.states[0]["issue"]["title"] == "Crash on empty input"
    assert asker.states[0]["issue"]["labels"] == ["bug"]


def test_review_sends_diff_and_maps_answers() -> None:
    asker = FakeAsker(review_answers())

    review = make_gate(asker).review(ISSUE, "--- a\n+++ b\n")

    assert review.addresses_issue.value is True
    assert review.risk.value == 0.3
    assert asker.states[0]["diff"] == "--- a\n+++ b\n"
    assert set(asker.questions[0]) == {"addresses", "risk"}


def test_long_issue_body_and_diff_are_truncated() -> None:
    asker = FakeAsker(review_answers())
    big = Issue(ref=ISSUE.ref, title="t", body="x" * 50, labels=())

    make_gate(asker, max_issue_chars=10, max_diff_chars=5).review(big, "y" * 20)

    state = asker.states[0]
    assert state["issue"]["body"] == "x" * 10 + "\n[truncated]"
    assert state["diff"] == "y" * 5 + "\n[truncated]"


def test_unicode_and_empty_body_pass_through() -> None:
    asker = FakeAsker(triage_answers())
    odd = Issue(ref=ISSUE.ref, title="Fehler: Größe 😀", body="", labels=())

    make_gate(asker).triage(odd)

    assert asker.states[0]["issue"]["title"] == "Fehler: Größe 😀"
    assert asker.states[0]["issue"]["body"] == ""


def test_unknown_issue_kind_is_a_protocol_error() -> None:
    answers = triage_answers()
    answers["kind"] = ChoiceAnswer(
        type="choice", choice="epic", probabilities={"epic": 1.0}, confidence=0.9
    )

    with pytest.raises(JevProtocolError, match="unknown issue kind"):
        make_gate(FakeAsker(answers)).triage(ISSUE)


def test_wrong_answer_type_is_a_protocol_error() -> None:
    answers = review_answers()
    answers["addresses"] = ScoreAnswer(
        type="score", score=1.0, legend={}, probabilities={}, confidence=0.9
    )

    with pytest.raises(JevProtocolError, match="expected a noul answer"):
        make_gate(FakeAsker(answers)).review(ISSUE, "d")
