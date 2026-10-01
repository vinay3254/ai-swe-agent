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
from swe_agent.watchdog.gate import JevWatchGate

type AnyAnswer = NoulAnswer | ChoiceAnswer | ScoreAnswer


class FakeAsker:
    def __init__(self, answers: dict[str, AnyAnswer]) -> None:
        self.answers = answers
        self.states: list[Mapping[str, Any]] = []
        self.questions: list[Mapping[str, Question]] = []

    def ask(self, state: Mapping[str, Any], questions: Mapping[str, Question]) -> dict[str, AnyAnswer]:
        self.states.append(state)
        self.questions.append(questions)
        return self.answers


def score(value: float, conf: float = 0.9) -> ScoreAnswer:
    return ScoreAnswer(type="score", score=value, legend={}, probabilities={}, confidence=conf)


def test_derivation_returns_noul_and_similarity_decisions() -> None:
    asker = FakeAsker({"derived": NoulAnswer(type="noul", noul=0.97), "similarity": score(3.2)})

    result = JevWatchGate(asker, max_chars=100).derivation("orig code", "cand code")

    assert result.derived.value is True
    assert result.similarity.value == 3.2
    assert set(asker.questions[0]) == {"derived", "similarity"}
    assert asker.states[0] == {"original": "orig code", "candidate": "cand code"}


def test_attribution_returns_a_noul_decision_and_sends_the_holder_lines() -> None:
    asker = FakeAsker({"attributed": NoulAnswer(type="noul", noul=0.1)})

    decision = JevWatchGate(asker, max_chars=100).attribution(
        ["Copyright (c) 2024 Jane Doe"], "Portions by J. Doe"
    )

    assert decision.value is False
    assert asker.states[0]["original_copyright"] == ["Copyright (c) 2024 Jane Doe"]
    assert asker.states[0]["candidate_notice"] == "Portions by J. Doe"


def test_excerpts_are_truncated() -> None:
    asker = FakeAsker({"derived": NoulAnswer(type="noul", noul=0.9), "similarity": score(1)})

    JevWatchGate(asker, max_chars=10).derivation("a" * 50, "b" * 50)

    assert asker.states[0]["original"] == "a" * 10 + "\n[truncated]"
    assert asker.states[0]["candidate"] == "b" * 10 + "\n[truncated]"


def test_similarity_outside_its_scale_is_a_protocol_error() -> None:
    asker = FakeAsker({"derived": NoulAnswer(type="noul", noul=0.9), "similarity": score(9)})

    with pytest.raises(JevProtocolError, match="outside"):
        JevWatchGate(asker, max_chars=100).derivation("a", "b")
