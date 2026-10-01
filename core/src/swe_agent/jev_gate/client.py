import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 524, 529})
MAX_BACKOFF_S = 8.0


class JevError(Exception):
    """Base class. The pipeline catches this and escalates to a human."""


class JevAuthError(JevError):
    """HTTP 401. Retrying cannot help."""


class JevProtocolError(JevError):
    """Jev replied, but not with something we can use (422, bad JSON, missing answer)."""


class JevUnavailable(JevError):
    """Retries exhausted on timeouts, 429, 5xx or 529."""


class Question(BaseModel, frozen=True):
    type: Literal["noul", "choice", "score"]
    instructions: str
    criteria: dict[str, str] | list[str] | None = None


# Jev replies come from outside the process. Reject NaN, infinities and out-of-range
# values at the boundary so they cannot slip past a threshold comparison.
Probability = Annotated[float, Field(ge=0.0, le=1.0, allow_inf_nan=False)]
Score = Annotated[float, Field(ge=0.0, allow_inf_nan=False)]


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: Probability


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, Probability]
    confidence: Probability


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: Score
    legend: dict[str, str]
    probabilities: dict[str, Probability]
    confidence: Probability


Answer = Annotated[NoulAnswer | ChoiceAnswer | ScoreAnswer, Field(discriminator="type")]


class SystemOneResponse(BaseModel):
    model: str
    answers: dict[str, Answer]


class Asker(Protocol):
    def ask(
        self, state: Mapping[str, Any], questions: Mapping[str, Question]
    ) -> dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]: ...


class JevClient:
    """Thin typed wrapper over POST /v1/systemone with retry and backoff."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str,
        timeout_s: float,
        max_attempts: int,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._model = model
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_s,
        )

    def close(self) -> None:
        self._http.close()

    def ask(
        self, state: Mapping[str, Any], questions: Mapping[str, Question]
    ) -> dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]:
        body = {
            "state": state,
            "model": self._model,
            "questions": {k: q.model_dump(exclude_none=True) for k, q in questions.items()},
        }
        last_failure = "no attempt made"
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._http.post("/v1/systemone", json=body)
            except httpx.TransportError as exc:  # includes timeouts
                last_failure = repr(exc)
            else:
                if response.status_code == 401:
                    raise JevAuthError("Jev rejected the API key (HTTP 401)")
                if response.status_code == 422:
                    raise JevProtocolError(f"Jev rejected the request (HTTP 422): {response.text}")
                if response.status_code in RETRY_STATUS:
                    last_failure = f"HTTP {response.status_code}"
                elif response.is_success:
                    return self._parse(response, questions)
                else:
                    raise JevError(f"unexpected HTTP {response.status_code}: {response.text}")
            if attempt < self._max_attempts:
                self._sleep(min(0.5 * 2 ** (attempt - 1), MAX_BACKOFF_S))
        raise JevUnavailable(f"Jev unavailable after {self._max_attempts} attempts: {last_failure}")

    @staticmethod
    def _parse(
        response: httpx.Response, questions: Mapping[str, Question]
    ) -> dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]:
        try:
            parsed = SystemOneResponse.model_validate_json(response.content)
        except ValidationError as exc:
            raise JevProtocolError(f"malformed Jev response: {exc}") from exc
        for key, question in questions.items():
            answer = parsed.answers.get(key)
            if answer is None:
                raise JevProtocolError(f"Jev response is missing answer {key!r}")
            if answer.type != question.type:
                raise JevProtocolError(
                    f"answer {key!r} has type {answer.type!r}, expected {question.type!r}"
                )
        return parsed.answers
