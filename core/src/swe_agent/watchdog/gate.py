from typing import Protocol

from pydantic import BaseModel

from swe_agent.jev_gate.client import Asker, Question
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import as_noul, as_score

SIMILARITY_MAX = 4.0  # five criteria below

_DERIVATION = {
    "derived": Question(
        type="noul",
        instructions="Is the candidate code derived from the original code?",
        criteria={
            "true": "The candidate reuses the original's code or structure, even if renamed or reformatted",
            "false": "The candidate is independent work that happens to overlap in topic",
        },
    ),
    "similarity": Question(
        type="score",
        instructions="How similar is the candidate code to the original code?",
        criteria=[
            "Unrelated",
            "Same topic, different code",
            "Similar structure, different code",
            "Substantial copy with edits",
            "Verbatim or near-verbatim copy",
        ],
    ),
}

_ATTRIBUTION = {
    "attributed": Question(
        type="noul",
        instructions=(
            "Does the candidate notice text credit the original copyright holders listed in "
            "the state, even if worded differently?"
        ),
        criteria={
            "true": "The original holders are credited or the original work is clearly acknowledged",
            "false": "The original holders are not credited",
        },
    )
}


class Derivation(BaseModel, frozen=True):
    derived: Decision[bool]
    similarity: Decision[float]


class WatchGate(Protocol):
    def derivation(self, original: str, candidate: str) -> Derivation: ...
    def attribution(self, original_copyright: list[str], candidate_notice: str) -> Decision[bool]: ...


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "\n[truncated]"


class JevWatchGate:
    def __init__(self, asker: Asker, *, max_chars: int) -> None:
        self._asker = asker
        self._max = max_chars

    def derivation(self, original: str, candidate: str) -> Derivation:
        state = {"original": _clip(original, self._max), "candidate": _clip(candidate, self._max)}
        answers = self._asker.ask(state, _DERIVATION)
        return Derivation(
            derived=as_noul(answers["derived"]),
            similarity=as_score(answers["similarity"], upper=SIMILARITY_MAX),
        )

    def attribution(self, original_copyright: list[str], candidate_notice: str) -> Decision[bool]:
        state = {
            "original_copyright": original_copyright,
            "candidate_notice": _clip(candidate_notice, self._max),
        }
        return as_noul(self._asker.ask(state, _ATTRIBUTION)["attributed"])
