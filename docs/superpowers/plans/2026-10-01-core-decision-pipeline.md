# Core Decision Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the `core` Python package's decision layer: config, Jev client and typed gate, SQLite store, outcome rules, and the issue-fix pipeline state machine, all tested against fakes.

**Architecture:** The LLM-driven parts (OpenHands runner, GitHub) sit behind `Protocol` ports (`RunnerPort`, `GitHubPort`). Jev is reached through `JevClient` (HTTP) wrapped by `JevGate` (typed questions). Every Jev answer is a `Decision[T]` that must pass through `gate()` before its value is used, so low-confidence handling cannot be skipped. `Pipeline` composes these and ends every job in `done`, `escalated`, or `failed`.

**Tech Stack:** Python 3.13+, uv, Pydantic v2, pydantic-settings, httpx, SQLite (stdlib), pytest, respx, pyright (strict on `src`).

**Spec:** `docs/superpowers/specs/2026-10-01-ai-swe-agent-design.md` (sections 2, 3, 4, 6, 7 for this plan)

## Scope and sequencing

The spec covers four independently shippable subsystems. This is **Plan 1 of 4**. It produces a working, fully tested library with no network or Docker dependency.

| Plan | Delivers | Needs |
|---|---|---|
| **1 (this)** | config, `jev_gate`, `store`, outcome rules, `pipeline` with ports and fakes | nothing external |
| 2 | Real `GitHubPort` (PyGithub or httpx), real `RunnerPort` (OpenHands SDK + Docker), FastAPI `/jobs`, Typer `agent fix` | Plan 1, OpenHands SDK docs read first |
| 3 | `gateway` (TypeScript webhook receiver, signature check, `ai-fix` label filter, `openapi-typescript` types, CI drift check) | Plan 2's OpenAPI schema |
| 4 | `watchdog` (fork check, copy check, license check, Jev classification, case store, digest, owner-approved notice) | Plan 1 `jev_gate` and `store` |

## Global Constraints

- Python `>=3.13`; `pyright` strict on everything under `core/src`.
- Pydantic models for every value that crosses a module boundary.
- All Jev-driven cutoffs live in `config.Thresholds` and nowhere else.
- Every Jev answer is logged to the `decisions` table with its confidence.
- A Jev failure never falls through to "proceed": it escalates to a human (spec section 6).
- Test pass/fail is a deterministic input to the outcome, never a Jev question (spec section 4, step 4).
- Tests make no real network calls. HTTP is mocked with `respx`.
- The agent never pushes to the default branch; Plan 1 only fixes the branch name `ai-fix/issue-<number>`.

## Deviations from the spec (decided from the Jev API reference)

- **Noul has no `confidence` field.** The API returns only `{"type": "noul", "noul": 0.95}`. `noul_decision()` derives confidence as `abs(2p - 1)`: 0.5 gives 0.0, 0 or 1 gives 1.0. Choice and Score answers carry their own `confidence`.
- **Direct HTTP client instead of the `typesafe-sdk` package.** The API reference documents the endpoint, auth header, schemas and error codes (`POST https://api.typesafe.ai/v1/systemone`, `Authorization: Bearer <key>`). The SDK docs do not document `RetryPolicy` or confidence fields. `JevClient` implements retry for 429, 5xx and 529. Swapping to the SDK later only touches `client.py`.
- **Jev model name is a required setting** (`SWE_AGENT_JEV_MODEL`). The docs show `"model": "string"` and no default. Get the value from console.typesafe.ai.
- **Extra initial job state `received`.** The spec lists six states starting at `triaged`; a job row must exist before triage runs for idempotency.

## Review Focus

Failure modes the spec implies but does not spell out, most likely first. Each has a test in the owning task.

- Jev answers with the wrong type, an unknown choice label, or omits a question: must raise `JevProtocolError` and escalate, never crash or proceed. (Tasks 3, 4, 7)
- A Noul probability of exactly 0.5, NaN, or outside 0-1: confidence 0 and `Uncertain`, or `ValueError`. (Task 2)
- The agent returns an empty or whitespace-only diff: escalate, spend no Jev review call, open no PR. (Task 7)
- The same webhook delivered twice (same job key): one run, one PR. (Tasks 5, 7)
- Jev returns 401 (bad key): fail fast without retries, then escalate. (Tasks 3, 7)
- Also covered: very large or non-ASCII issue text (Task 4); GitHub failing while reporting an escalation (Task 7).

---

## File Structure

```
.gitignore
core/
  pyproject.toml
  src/swe_agent/
    __init__.py
    config.py              Thresholds, Settings, load_settings()
    models.py              IssueRef, Issue, JobState
    store.py               SQLite: jobs, decision log
    outcome.py             triage_blocker(), decide_outcome() pure rules
    pipeline.py            ports, RunRequest/RunResult, Pipeline
    jev_gate/
      __init__.py
      decision.py          Decision[T], gate(), noul_decision()
      client.py            JevClient (HTTP + retry), answer models, errors
      questions.py         Triage/Review types, question text, JevGate
  tests/
    fakes.py               FakeGate, FakeRunner, FakeGitHub
    test_*.py
```

---

### Task 1: Project scaffold and config

**Files:**
- Create: `.gitignore`
- Create: `core/pyproject.toml`
- Create: `core/src/swe_agent/__init__.py` (empty)
- Create: `core/src/swe_agent/config.py`
- Test: `core/tests/test_config.py`

**Interfaces:**
- Produces: `Thresholds` (fields `triage_min_confidence`, `max_complexity`, `review_min_confidence`, `max_ready_risk`), `Settings`, `load_settings() -> Settings`.

- [ ] **Step 1: Create the scaffold and the failing test**

Create `.gitignore`:

```
.venv/
__pycache__/
.pytest_cache/
*.db
```

Create `core/pyproject.toml`:

```toml
[project]
name = "swe-agent-core"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = [
    "pydantic>=2.12",
    "pydantic-settings>=2.6",
    "httpx>=0.28",
]

[dependency-groups]
dev = [
    "pytest>=8",
    "respx>=0.22",
    "pyright>=1.1.400",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/swe_agent"]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.pyright]
include = ["src", "tests"]
strict = ["src"]
extraPaths = ["src", "tests"]
pythonVersion = "3.13"
```

Create an empty `core/src/swe_agent/__init__.py`, then create `core/tests/test_config.py`:

```python
import pytest
from pydantic import ValidationError

from swe_agent.config import Thresholds, load_settings


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "secret-key")
    monkeypatch.setenv("SWE_AGENT_JEV_MODEL", "jev-test")
    monkeypatch.setenv("SWE_AGENT_REPO_ALLOWLIST", '["octo/app"]')
    monkeypatch.setenv("SWE_AGENT_THRESHOLDS__MAX_COMPLEXITY", "3.5")

    settings = load_settings()

    assert settings.typesafe_api_key.get_secret_value() == "secret-key"
    assert settings.jev_model == "jev-test"
    assert settings.repo_allowlist == ["octo/app"]
    assert settings.thresholds.max_complexity == 3.5
    assert "secret-key" not in repr(settings)


def test_settings_require_model_name(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TYPESAFE_API_KEY", "k")
    monkeypatch.delenv("SWE_AGENT_JEV_MODEL", raising=False)

    with pytest.raises(ValidationError):
        load_settings()


def test_thresholds_reject_out_of_range_values() -> None:
    with pytest.raises(ValidationError):
        Thresholds.model_validate({"triage_min_confidence": 1.5})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd core && uv sync && uv run pytest tests/test_config.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'swe_agent.config'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/config.py`:

```python
from pathlib import Path

from pydantic import BaseModel, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Thresholds(BaseModel, frozen=True):
    """Every Jev-driven cutoff in one place. Tune these from the decision log."""

    triage_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Complexity is a Jev score on a 0-4 scale (trivial .. epic).
    max_complexity: float = Field(default=2.0, ge=0.0, le=4.0)
    review_min_confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    # Risk is a Jev score on a 0-2 scale (low .. high). Above this, the PR is a draft.
    max_ready_risk: float = Field(default=0.5, ge=0.0, le=2.0)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SWE_AGENT_", env_nested_delimiter="__")

    # The SDK documents TYPESAFE_API_KEY, so this field ignores the SWE_AGENT_ prefix.
    typesafe_api_key: SecretStr = Field(validation_alias="TYPESAFE_API_KEY")
    # Model name comes from console.typesafe.ai. No default on purpose.
    jev_model: str
    jev_base_url: str = "https://api.typesafe.ai"
    jev_timeout_s: float = Field(default=30.0, gt=0)
    jev_max_attempts: int = Field(default=4, ge=1)
    max_issue_chars: int = Field(default=20_000, gt=0)
    max_diff_chars: int = Field(default=60_000, gt=0)
    repo_allowlist: list[str] = Field(default_factory=list)
    db_path: Path = Path("swe_agent.db")
    thresholds: Thresholds = Field(default_factory=Thresholds)


def load_settings() -> Settings:
    """Build Settings from the environment. Pyright cannot see env-sourced fields."""
    return Settings()  # pyright: ignore[reportCallIssue]
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: `3 passed`, then `0 errors, 0 warnings, 0 informations`

- [ ] **Step 5: Commit**

```bash
git add .gitignore core
git commit -m "feat(core): scaffold package and typed settings"
```

---

### Task 2: Decision type and confidence gate

**Files:**
- Create: `core/src/swe_agent/jev_gate/__init__.py` (empty)
- Create: `core/src/swe_agent/jev_gate/decision.py`
- Test: `core/tests/test_jev_decision.py`

**Interfaces:**
- Produces:
  - `Decision[T]` with `value: T`, `confidence: float` (0-1), `probabilities: dict[str, float]`
  - `Confident[T](value, confidence)`, `Uncertain(confidence, threshold)`, `type Gated[T] = Confident[T] | Uncertain`
  - `gate(decision: Decision[T], min_confidence: float) -> Gated[T]`
  - `noul_decision(p_true: float) -> Decision[bool]` (raises `ValueError` outside 0-1 or NaN)

- [ ] **Step 1: Write the failing test**

Create `core/tests/test_jev_decision.py`:

```python
import math

import pytest
from pydantic import ValidationError

from swe_agent.jev_gate.decision import Confident, Decision, Uncertain, gate, noul_decision


def test_noul_high_probability_is_confident_true() -> None:
    d = noul_decision(0.95)
    assert d.value is True
    assert d.confidence == pytest.approx(0.9)
    assert d.probabilities == {"true": 0.95, "false": pytest.approx(0.05)}


def test_noul_low_probability_is_confident_false() -> None:
    d = noul_decision(0.05)
    assert d.value is False
    assert d.confidence == pytest.approx(0.9)


def test_noul_coin_flip_has_zero_confidence_and_is_uncertain() -> None:
    d = noul_decision(0.5)
    assert d.confidence == 0.0
    assert isinstance(gate(d, 0.01), Uncertain)


@pytest.mark.parametrize("bad", [-0.1, 1.2, math.nan, math.inf])
def test_noul_rejects_invalid_probability(bad: float) -> None:
    with pytest.raises(ValueError):
        noul_decision(bad)


def test_gate_passes_at_exact_threshold() -> None:
    d = Decision[str](value="bug", confidence=0.7, probabilities={"bug": 1.0})
    assert gate(d, 0.7) == Confident("bug", 0.7)


def test_gate_reports_uncertain_below_threshold() -> None:
    d = Decision[str](value="bug", confidence=0.69, probabilities={"bug": 1.0})
    assert gate(d, 0.7) == Uncertain(confidence=0.69, threshold=0.7)


def test_decision_rejects_confidence_above_one() -> None:
    with pytest.raises(ValidationError):
        Decision[bool](value=True, confidence=1.01, probabilities={})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd core && uv run pytest tests/test_jev_decision.py -v`
Expected: `ModuleNotFoundError: No module named 'swe_agent.jev_gate'`

- [ ] **Step 3: Write the implementation**

Create an empty `core/src/swe_agent/jev_gate/__init__.py`, then `core/src/swe_agent/jev_gate/decision.py`:

```python
from dataclasses import dataclass

from pydantic import BaseModel, Field


class Decision[T](BaseModel, frozen=True):
    """One typed Jev answer. Read `value` only through `gate`."""

    value: T
    confidence: float = Field(ge=0.0, le=1.0)
    probabilities: dict[str, float]


@dataclass(frozen=True)
class Confident[T]:
    value: T
    confidence: float


@dataclass(frozen=True)
class Uncertain:
    confidence: float
    threshold: float


type Gated[T] = Confident[T] | Uncertain


def gate[T](decision: Decision[T], min_confidence: float) -> Gated[T]:
    """Force the caller to handle low confidence: the result is a union, not a bare value."""
    if decision.confidence >= min_confidence:
        return Confident(decision.value, decision.confidence)
    return Uncertain(decision.confidence, min_confidence)


def noul_decision(p_true: float) -> Decision[bool]:
    """Turn a Noul probability into a Decision.

    The API returns only P(true) for Noul, with no confidence field. Confidence is
    the distance from a coin flip: 0.5 gives 0.0, 0.0 or 1.0 gives 1.0.
    """
    if not 0.0 <= p_true <= 1.0:  # also rejects NaN
        raise ValueError(f"noul probability out of range: {p_true}")
    return Decision(
        value=p_true >= 0.5,
        confidence=abs(2.0 * p_true - 1.0),
        probabilities={"true": p_true, "false": 1.0 - p_true},
    )
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: all pass (`13 passed`), `0 errors`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add Decision type and confidence gate"
```

---

### Task 3: Jev HTTP client

**Files:**
- Create: `core/src/swe_agent/jev_gate/client.py`
- Test: `core/tests/test_jev_client.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces:
  - Errors: `JevError` (base), `JevAuthError`, `JevProtocolError`, `JevUnavailable`
  - `Question(type: "noul"|"choice"|"score", instructions: str, criteria: dict[str,str] | list[str] | None)`
  - Answer models `NoulAnswer(noul)`, `ChoiceAnswer(choice, probabilities, confidence)`, `ScoreAnswer(score, legend, probabilities, confidence)`, each with a `type` literal
  - `Asker` protocol: `ask(state: Mapping[str, Any], questions: Mapping[str, Question]) -> dict[str, NoulAnswer | ChoiceAnswer | ScoreAnswer]`
  - `JevClient(*, api_key, model, base_url, timeout_s, max_attempts, sleep=time.sleep)` implementing `Asker`, plus `close()`

Behavior: retry on timeouts, transport errors, 429, 500, 502, 503, 504, 529 with sleeps of 0.5s, 1s, 2s ... capped at 8s. Fail fast on 401 (`JevAuthError`) and 422 (`JevProtocolError`). A reply missing a requested answer, with a mismatched answer type, or with a malformed body raises `JevProtocolError`.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_jev_client.py`:

```python
import json

import httpx
import pytest
import respx

from swe_agent.jev_gate.client import (
    ChoiceAnswer,
    JevAuthError,
    JevClient,
    JevProtocolError,
    JevUnavailable,
    NoulAnswer,
    Question,
)

BASE = "https://api.jev.test"
QUESTIONS = {
    "ok": Question(type="noul", instructions="Is it fine?"),
    "kind": Question(type="choice", instructions="Which?", criteria={"a": "A", "b": "B"}),
}
GOOD_BODY = {
    "model": "jev-test",
    "answers": {
        "ok": {"type": "noul", "noul": 0.9},
        "kind": {
            "type": "choice",
            "choice": "a",
            "probabilities": {"a": 0.8, "b": 0.2},
            "confidence": 0.7,
        },
    },
    "usage": {"input_tokens": 1, "output_tokens": 1},
}


@pytest.fixture
def sleeps() -> list[float]:
    return []


@pytest.fixture
def client(sleeps: list[float]) -> JevClient:
    return JevClient(
        api_key="key-123",
        model="jev-test",
        base_url=BASE,
        timeout_s=5,
        max_attempts=3,
        sleep=sleeps.append,
    )


@respx.mock
def test_ask_sends_documented_request_and_parses_answers(client: JevClient) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(
        return_value=httpx.Response(200, json=GOOD_BODY)
    )

    answers = client.ask({"issue": "text"}, QUESTIONS)

    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer key-123"
    sent = json.loads(request.content)
    assert sent["model"] == "jev-test"
    assert sent["state"] == {"issue": "text"}
    assert sent["questions"]["ok"] == {"type": "noul", "instructions": "Is it fine?"}
    assert sent["questions"]["kind"]["criteria"] == {"a": "A", "b": "B"}
    assert answers["ok"] == NoulAnswer(type="noul", noul=0.9)
    kind = answers["kind"]
    assert isinstance(kind, ChoiceAnswer) and kind.choice == "a"


@respx.mock
def test_retries_overload_then_succeeds(client: JevClient, sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(
        side_effect=[httpx.Response(529), httpx.Response(200, json=GOOD_BODY)]
    )

    client.ask({}, QUESTIONS)

    assert route.call_count == 2
    assert sleeps == [0.5]


@respx.mock
def test_gives_up_after_max_attempts(client: JevClient, sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(return_value=httpx.Response(429))

    with pytest.raises(JevUnavailable, match="3 attempts"):
        client.ask({}, QUESTIONS)

    assert route.call_count == 3
    assert sleeps == [0.5, 1.0]


@respx.mock
def test_transport_errors_are_retried(client: JevClient) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(side_effect=httpx.ConnectError("boom"))

    with pytest.raises(JevUnavailable):
        client.ask({}, QUESTIONS)

    assert route.call_count == 3


@respx.mock
def test_auth_failure_is_not_retried(client: JevClient, sleeps: list[float]) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(return_value=httpx.Response(401))

    with pytest.raises(JevAuthError):
        client.ask({}, QUESTIONS)

    assert route.call_count == 1
    assert sleeps == []


@respx.mock
def test_validation_failure_is_not_retried(client: JevClient) -> None:
    route = respx.post(f"{BASE}/v1/systemone").mock(
        return_value=httpx.Response(422, text="bad question")
    )

    with pytest.raises(JevProtocolError, match="bad question"):
        client.ask({}, QUESTIONS)

    assert route.call_count == 1


@respx.mock
def test_missing_answer_is_a_protocol_error(client: JevClient) -> None:
    body = {**GOOD_BODY, "answers": {"ok": GOOD_BODY["answers"]["ok"]}}
    respx.post(f"{BASE}/v1/systemone").mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(JevProtocolError, match="missing answer 'kind'"):
        client.ask({}, QUESTIONS)


@respx.mock
def test_wrong_answer_type_is_a_protocol_error(client: JevClient) -> None:
    body = {**GOOD_BODY, "answers": {**GOOD_BODY["answers"], "ok": {"type": "noul", "noul": 0.1}}}
    body["answers"]["kind"] = {"type": "noul", "noul": 0.5}
    respx.post(f"{BASE}/v1/systemone").mock(return_value=httpx.Response(200, json=body))

    with pytest.raises(JevProtocolError, match="expected 'choice'"):
        client.ask({}, QUESTIONS)


@respx.mock
def test_non_json_body_is_a_protocol_error(client: JevClient) -> None:
    respx.post(f"{BASE}/v1/systemone").mock(return_value=httpx.Response(200, text="<html>"))

    with pytest.raises(JevProtocolError, match="malformed"):
        client.ask({}, QUESTIONS)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && uv run pytest tests/test_jev_client.py -v`
Expected: `ModuleNotFoundError: No module named 'swe_agent.jev_gate.client'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/jev_gate/client.py`:

```python
import time
from collections.abc import Callable, Mapping
from typing import Annotated, Any, Literal, Protocol

import httpx
from pydantic import BaseModel, Field, ValidationError

RETRY_STATUS = frozenset({429, 500, 502, 503, 504, 529})
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


class NoulAnswer(BaseModel):
    type: Literal["noul"]
    noul: float


class ChoiceAnswer(BaseModel):
    type: Literal["choice"]
    choice: str
    probabilities: dict[str, float]
    confidence: float


class ScoreAnswer(BaseModel):
    type: Literal["score"]
    score: float
    legend: dict[str, str]
    probabilities: dict[str, float]
    confidence: float


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
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: all pass (`22 passed`), `0 errors`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add Jev HTTP client with retry and fail-fast auth"
```

---

### Task 4: Domain models and typed Jev questions

**Files:**
- Create: `core/src/swe_agent/models.py`
- Create: `core/src/swe_agent/jev_gate/questions.py`
- Test: `core/tests/test_models.py`
- Test: `core/tests/test_jev_questions.py`

**Interfaces:**
- Consumes: `Asker`, `Question`, answer models, `JevProtocolError` (Task 3); `Decision`, `noul_decision` (Task 2).
- Produces:
  - `IssueRef(owner, repo, number)` with `IssueRef.parse(url)`, `.full_name`, `.url`; `Issue(ref, title, body, labels)`; `JobState` (`RECEIVED, TRIAGED, RUNNING, REVIEWED, DONE, ESCALATED, FAILED`)
  - `IssueKind` (`BUG, FEATURE, QUESTION, UNCLEAR`)
  - `Triage(kind: Decision[IssueKind], complexity: Decision[float], fixable: Decision[bool])`
  - `Review(addresses_issue: Decision[bool], risk: Decision[float])`
  - `Gate` protocol: `triage(issue: Issue) -> Triage`, `review(issue: Issue, diff: str) -> Review`
  - `JevGate(asker, *, max_issue_chars: int, max_diff_chars: int)` implementing `Gate`

Scales: complexity 0 (trivial) to 4 (epic); risk 0 (low) to 2 (high).

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_models.py`:

```python
import pytest

from swe_agent.models import IssueRef


def test_parse_issue_url() -> None:
    ref = IssueRef.parse("https://github.com/octo/app.js/issues/42")
    assert (ref.owner, ref.repo, ref.number) == ("octo", "app.js", 42)
    assert ref.full_name == "octo/app.js"
    assert ref.url == "https://github.com/octo/app.js/issues/42"


def test_parse_accepts_trailing_slash_and_whitespace() -> None:
    assert IssueRef.parse(" https://github.com/octo/app/issues/7/\n").number == 7


@pytest.mark.parametrize(
    "bad",
    [
        "https://github.com/octo/app/pull/7",
        "https://gitlab.com/octo/app/issues/7",
        "https://github.com/octo/app/issues/0x",
        "https://github.com/octo/app/issues/7/comments",
        "octo/app#7",
        "",
    ],
)
def test_parse_rejects_other_urls(bad: str) -> None:
    with pytest.raises(ValueError):
        IssueRef.parse(bad)
```

Create `core/tests/test_jev_questions.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && uv run pytest tests/test_models.py tests/test_jev_questions.py -v`
Expected: `ModuleNotFoundError: No module named 'swe_agent.models'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/models.py`:

```python
import re
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field

_ISSUE_URL = re.compile(r"^https://github\.com/([\w.-]+)/([\w.-]+)/issues/(\d+)/?$")


class IssueRef(BaseModel, frozen=True):
    owner: str
    repo: str
    number: int = Field(gt=0)

    @classmethod
    def parse(cls, url: str) -> Self:
        match = _ISSUE_URL.match(url.strip())
        if match is None:
            raise ValueError(f"not a GitHub issue URL: {url!r}")
        return cls(owner=match[1], repo=match[2], number=int(match[3]))

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.repo}"

    @property
    def url(self) -> str:
        return f"https://github.com/{self.full_name}/issues/{self.number}"


class Issue(BaseModel, frozen=True):
    ref: IssueRef
    title: str
    body: str
    labels: tuple[str, ...] = ()


class JobState(StrEnum):
    RECEIVED = "received"  # job row exists, nothing has run yet
    TRIAGED = "triaged"
    RUNNING = "running"
    REVIEWED = "reviewed"
    DONE = "done"
    ESCALATED = "escalated"
    FAILED = "failed"
```

Create `core/src/swe_agent/jev_gate/questions.py`:

```python
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
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: all pass (`36 passed`), `0 errors`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add domain models and typed Jev triage and review questions"
```

---

### Task 5: SQLite store

**Files:**
- Create: `core/src/swe_agent/store.py`
- Test: `core/tests/test_store.py`

**Interfaces:**
- Consumes: `JobState` (Task 4).
- Produces:
  - `Job(key, issue_url, state, pr_url, reason, created_at, updated_at)`, `DecisionRecord(job_key, question, value, confidence, created_at)`
  - `Store(path: Path | str)` with:
    - `begin_job(key: str, issue_url: str) -> tuple[Job, bool]` (bool is True only when this call created the job)
    - `get_job(key) -> Job | None`
    - `set_state(key, state, *, pr_url=None, reason=None)` (raises `KeyError` for an unknown key; `None` keeps the stored value)
    - `log_decision(key, question, value, confidence)`
    - `decisions(key) -> list[DecisionRecord]` in insertion order
    - `close()`

Note for Plan 2: `sqlite3` connections are bound to their creating thread. FastAPI workers will need one `Store` per thread or `check_same_thread=False` plus a lock.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_store.py`:

```python
import pytest

from swe_agent.models import JobState
from swe_agent.store import Store


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


def test_begin_job_creates_received_job(store: Store) -> None:
    job, created = store.begin_job("k1", "https://github.com/o/r/issues/1")

    assert created is True
    assert job.state is JobState.RECEIVED
    assert job.pr_url is None


def test_begin_job_with_same_key_is_idempotent(store: Store) -> None:
    store.begin_job("k1", "https://github.com/o/r/issues/1")
    store.set_state("k1", JobState.RUNNING)

    job, created = store.begin_job("k1", "https://github.com/o/r/issues/1")

    assert created is False
    assert job.state is JobState.RUNNING


def test_set_state_keeps_earlier_pr_url_and_reason(store: Store) -> None:
    store.begin_job("k1", "u")
    store.set_state("k1", JobState.DONE, pr_url="https://github.com/o/r/pull/9", reason="ok")
    store.set_state("k1", JobState.DONE)

    job = store.get_job("k1")
    assert job is not None
    assert job.pr_url == "https://github.com/o/r/pull/9"
    assert job.reason == "ok"


def test_set_state_on_unknown_job_raises(store: Store) -> None:
    with pytest.raises(KeyError):
        store.set_state("missing", JobState.FAILED)


def test_get_job_returns_none_for_unknown_key(store: Store) -> None:
    assert store.get_job("missing") is None


def test_decisions_round_trip_in_insertion_order(store: Store) -> None:
    store.begin_job("k1", "u")
    store.log_decision("k1", "kind", "bug", 0.85)
    store.log_decision("k1", "fixable", True, 0.8)

    records = store.decisions("k1")

    assert [(r.question, r.value, r.confidence) for r in records] == [
        ("kind", "bug", 0.85),
        ("fixable", True, 0.8),
    ]
    assert store.decisions("other") == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && uv run pytest tests/test_store.py -v`
Expected: `ModuleNotFoundError: No module named 'swe_agent.store'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/store.py`:

```python
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from swe_agent.models import JobState

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    key TEXT PRIMARY KEY,
    issue_url TEXT NOT NULL,
    state TEXT NOT NULL,
    pr_url TEXT,
    reason TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_key TEXT NOT NULL REFERENCES jobs(key),
    question TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT NOT NULL
);
"""


class Job(BaseModel, frozen=True):
    key: str
    issue_url: str
    state: JobState
    pr_url: str | None
    reason: str | None
    created_at: str
    updated_at: str


class DecisionRecord(BaseModel, frozen=True):
    job_key: str
    question: str
    value: object
    confidence: float
    created_at: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Store:
    """SQLite persistence for jobs and the Jev decision log."""

    def __init__(self, path: Path | str) -> None:
        self._db = sqlite3.connect(str(path))
        self._db.row_factory = sqlite3.Row
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    def begin_job(self, key: str, issue_url: str) -> tuple[Job, bool]:
        """Create the job if `key` is new. The bool is True only for the caller that created it."""
        now = _now()
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO jobs (key, issue_url, state, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, issue_url, JobState.RECEIVED.value, now, now),
            )
        job = self.get_job(key)
        assert job is not None
        return job, cursor.rowcount == 1

    def get_job(self, key: str) -> Job | None:
        row = self._db.execute("SELECT * FROM jobs WHERE key = ?", (key,)).fetchone()
        return None if row is None else Job.model_validate(dict(row))

    def set_state(
        self,
        key: str,
        state: JobState,
        *,
        pr_url: str | None = None,
        reason: str | None = None,
    ) -> None:
        with self._db:
            cursor = self._db.execute(
                "UPDATE jobs SET state = ?, pr_url = COALESCE(?, pr_url), "
                "reason = COALESCE(?, reason), updated_at = ? WHERE key = ?",
                (state.value, pr_url, reason, _now(), key),
            )
        if cursor.rowcount == 0:
            raise KeyError(key)

    def log_decision(self, key: str, question: str, value: object, confidence: float) -> None:
        with self._db:
            self._db.execute(
                "INSERT INTO decisions (job_key, question, value, confidence, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (key, question, json.dumps(value), confidence, _now()),
            )

    def decisions(self, key: str) -> list[DecisionRecord]:
        rows = self._db.execute(
            "SELECT job_key, question, value, confidence, created_at "
            "FROM decisions WHERE job_key = ? ORDER BY id",
            (key,),
        ).fetchall()
        return [
            DecisionRecord(
                job_key=r["job_key"],
                question=r["question"],
                value=json.loads(r["value"]),
                confidence=r["confidence"],
                created_at=r["created_at"],
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: all pass (`42 passed`), `0 errors`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add SQLite store with idempotent job creation and decision log"
```

---

### Task 6: Outcome rules

**Files:**
- Create: `core/src/swe_agent/outcome.py`
- Test: `core/tests/test_outcome.py`

**Interfaces:**
- Consumes: `Thresholds` (Task 1); `gate`, `Uncertain` (Task 2); `Triage`, `Review`, `IssueKind` (Task 4).
- Produces:
  - `Outcome` (`READY_PR, DRAFT_PR, ESCALATE`), `Verdict(outcome, reason)`
  - `triage_blocker(triage: Triage, t: Thresholds) -> str | None` (reason to stop, or `None` to proceed)
  - `decide_outcome(*, review: Review, tests_passed: bool, incomplete: bool, thresholds: Thresholds) -> Verdict`

Rules, in order: review unsure or says "does not address" escalates; then an incomplete run or failing tests gives a draft; then low-confidence or high risk gives a draft; otherwise ready.

- [ ] **Step 1: Write the failing tests**

Create `core/tests/test_outcome.py`:

```python
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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && uv run pytest tests/test_outcome.py -v`
Expected: `ModuleNotFoundError: No module named 'swe_agent.outcome'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/outcome.py`:

```python
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
```

- [ ] **Step 4: Run tests and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: all pass (`59 passed`), `0 errors`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add triage blockers and PR outcome rules"
```

---

### Task 7: Pipeline state machine

**Files:**
- Create: `core/src/swe_agent/pipeline.py`
- Create: `core/tests/fakes.py`
- Test: `core/tests/test_pipeline.py`

**Interfaces:**
- Consumes: `Store`, `Job` (Task 5); `Gate` (Task 4); `triage_blocker`, `decide_outcome`, `Outcome`, `Verdict` (Task 6); `JevError` (Task 3); `Thresholds` (Task 1); `Issue`, `IssueRef`, `JobState` (Task 4).
- Produces (Plan 2 implements the ports; Plans 2 and 4 call `Pipeline`):
  - `RunRequest(issue: Issue, branch: str)`; `RunResult(branch, diff, tests_passed, incomplete, transcript)`
  - `GitHubPort`: `fetch_issue(ref)`, `comment(ref, body)`, `add_label(ref, label)`, `open_pr(ref, *, branch, title, body, draft, labels) -> str`
  - `RunnerPort`: `run(request: RunRequest) -> RunResult`
  - `Pipeline(*, store, github, runner, gate, thresholds)` with `run(ref: IssueRef, job_key: str) -> Job`
  - Constants `NEEDS_HUMAN = "needs-human"`, `INCOMPLETE = "incomplete"`

Behavior: `run()` is idempotent per `job_key`. `JevError` anywhere ends in `escalated`. Any other exception ends in `failed`. The end state is saved before GitHub is told, so a GitHub error cannot erase it.

- [ ] **Step 1: Write the fakes and the failing tests**

Create `core/tests/fakes.py`:

```python
from dataclasses import dataclass, field

from swe_agent.jev_gate.client import JevUnavailable
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import IssueKind, Review, Triage
from swe_agent.models import Issue, IssueRef
from swe_agent.pipeline import RunRequest, RunResult


def good_triage() -> Triage:
    return Triage(
        kind=Decision(value=IssueKind.BUG, confidence=0.9, probabilities={}),
        complexity=Decision(value=1.0, confidence=0.9, probabilities={}),
        fixable=Decision(value=True, confidence=0.9, probabilities={}),
    )


def good_review() -> Review:
    return Review(
        addresses_issue=Decision(value=True, confidence=0.9, probabilities={}),
        risk=Decision(value=0.2, confidence=0.9, probabilities={}),
    )


def good_result() -> RunResult:
    return RunResult(
        branch="ai-fix/issue-1",
        diff="--- a/x.py\n+++ b/x.py\n@@\n-bug\n+fix\n",
        tests_passed=True,
        incomplete=False,
        transcript="",
    )


@dataclass
class FakeGate:
    triage_result: Triage | Exception = field(default_factory=good_triage)
    review_result: Review | Exception = field(default_factory=good_review)
    review_calls: int = 0

    def triage(self, issue: Issue) -> Triage:
        if isinstance(self.triage_result, Exception):
            raise self.triage_result
        return self.triage_result

    def review(self, issue: Issue, diff: str) -> Review:
        self.review_calls += 1
        if isinstance(self.review_result, Exception):
            raise self.review_result
        return self.review_result


@dataclass
class FakeRunner:
    result: RunResult | Exception = field(default_factory=good_result)
    calls: list[RunRequest] = field(default_factory=list)

    def run(self, request: RunRequest) -> RunResult:
        self.calls.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class PullRequest:
    branch: str
    title: str
    body: str
    draft: bool
    labels: list[str]


@dataclass
class FakeGitHub:
    comment_error: Exception | None = None
    comments: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    prs: list[PullRequest] = field(default_factory=list)

    def fetch_issue(self, ref: IssueRef) -> Issue:
        return Issue(ref=ref, title="Crash on empty input", body="parse('') raises", labels=())

    def comment(self, ref: IssueRef, body: str) -> None:
        if self.comment_error is not None:
            raise self.comment_error
        self.comments.append(body)

    def add_label(self, ref: IssueRef, label: str) -> None:
        self.labels.append(label)

    def open_pr(
        self, ref: IssueRef, *, branch: str, title: str, body: str, draft: bool, labels: list[str]
    ) -> str:
        self.prs.append(PullRequest(branch, title, body, draft, labels))
        return f"https://github.com/{ref.full_name}/pull/{100 + len(self.prs)}"


def jev_down() -> JevUnavailable:
    return JevUnavailable("Jev unavailable after 4 attempts: HTTP 529")
```

Create `core/tests/test_pipeline.py`:

```python
import pytest
from fakes import FakeGate, FakeGitHub, FakeRunner, good_result, good_triage, jev_down

from swe_agent.config import Thresholds
from swe_agent.jev_gate.client import JevAuthError
from swe_agent.jev_gate.decision import Decision
from swe_agent.jev_gate.questions import IssueKind, Triage
from swe_agent.models import IssueRef, JobState
from swe_agent.pipeline import Pipeline
from swe_agent.store import Store

REF = IssueRef(owner="octo", repo="app", number=1)


@pytest.fixture
def store() -> Store:
    return Store(":memory:")


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def runner() -> FakeRunner:
    return FakeRunner()


@pytest.fixture
def gate() -> FakeGate:
    return FakeGate()


@pytest.fixture
def pipeline(store: Store, github: FakeGitHub, runner: FakeRunner, gate: FakeGate) -> Pipeline:
    return Pipeline(
        store=store, github=github, runner=runner, gate=gate, thresholds=Thresholds()
    )


def test_happy_path_opens_a_ready_pr(
    pipeline: Pipeline, store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.DONE
    assert job.pr_url == "https://github.com/octo/app/pull/101"
    assert len(github.prs) == 1
    pr = github.prs[0]
    assert (pr.draft, pr.labels, pr.branch) == (False, [], "ai-fix/issue-1")
    assert "Resolves #1" in pr.body
    assert github.labels == []
    assert runner.calls[0].branch == "ai-fix/issue-1"
    assert [d.question for d in store.decisions("job-1")] == [
        "kind",
        "complexity",
        "fixable",
        "addresses_issue",
        "risk",
    ]


def test_low_confidence_triage_escalates_without_running_the_agent(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    unsure = Triage(
        kind=Decision(value=IssueKind.BUG, confidence=0.3, probabilities={}),
        complexity=good_triage().complexity,
        fixable=good_triage().fixable,
    )
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=unsure),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert runner.calls == []
    assert github.labels == ["needs-human"]
    assert "unsure" in github.comments[0]
    assert github.prs == []


def test_jev_down_at_triage_escalates_instead_of_proceeding(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=jev_down()),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert job.reason is not None and "Jev unavailable" in job.reason
    assert runner.calls == [] and github.prs == []
    assert github.labels == ["needs-human"]


def test_jev_auth_error_at_review_escalates_and_opens_no_pr(
    store: Store, github: FakeGitHub, runner: FakeRunner
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(review_result=JevAuthError("HTTP 401")),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert github.prs == []


def test_runner_crash_marks_job_failed_and_comments(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=RuntimeError("sandbox died")),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.FAILED
    assert job.reason == "RuntimeError: sandbox died"
    assert "sandbox died" in github.comments[0]
    assert github.labels == []


def test_empty_diff_escalates_and_skips_the_review_gate(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    empty = good_result().model_copy(update={"diff": "  \n"})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=empty),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert gate.review_calls == 0
    assert github.prs == []
    assert "no changes" in github.comments[0]


def test_replayed_job_key_does_nothing_the_second_time(
    pipeline: Pipeline, github: FakeGitHub, runner: FakeRunner
) -> None:
    first = pipeline.run(REF, "job-1")
    second = pipeline.run(REF, "job-1")

    assert second == first
    assert len(runner.calls) == 1
    assert len(github.prs) == 1


def test_incomplete_run_opens_a_labeled_draft_pr(
    store: Store, github: FakeGitHub, gate: FakeGate
) -> None:
    partial = good_result().model_copy(update={"incomplete": True})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=partial),
        gate=gate,
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.DONE
    assert github.prs[0].draft is True
    assert github.prs[0].labels == ["incomplete"]


def test_failing_tests_open_a_draft_pr(store: Store, github: FakeGitHub, gate: FakeGate) -> None:
    failing = good_result().model_copy(update={"tests_passed": False})
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=FakeRunner(result=failing),
        gate=gate,
        thresholds=Thresholds(),
    )

    pipeline.run(REF, "job-1")

    assert github.prs[0].draft is True
    assert "Tests: failed" in github.prs[0].body


def test_github_comment_failure_does_not_hide_the_escalation(
    store: Store, runner: FakeRunner
) -> None:
    github = FakeGitHub(comment_error=RuntimeError("GitHub 502"))
    pipeline = Pipeline(
        store=store,
        github=github,
        runner=runner,
        gate=FakeGate(triage_result=jev_down()),
        thresholds=Thresholds(),
    )

    job = pipeline.run(REF, "job-1")

    assert job.state is JobState.ESCALATED
    assert job.reason is not None and "Jev unavailable" in job.reason
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd core && uv run pytest tests/test_pipeline.py -v`
Expected: collection error `ModuleNotFoundError: No module named 'swe_agent.pipeline'`

- [ ] **Step 3: Write the implementation**

Create `core/src/swe_agent/pipeline.py`:

```python
import logging
from dataclasses import dataclass
from typing import Protocol

from pydantic import BaseModel

from swe_agent.config import Thresholds
from swe_agent.jev_gate.client import JevError
from swe_agent.jev_gate.questions import Gate
from swe_agent.models import Issue, IssueRef, JobState
from swe_agent.outcome import Outcome, Verdict, decide_outcome, triage_blocker
from swe_agent.store import Job, Store

log = logging.getLogger(__name__)

NEEDS_HUMAN = "needs-human"
INCOMPLETE = "incomplete"


@dataclass(frozen=True)
class RunRequest:
    issue: Issue
    branch: str


class RunResult(BaseModel, frozen=True):
    branch: str
    diff: str
    tests_passed: bool
    incomplete: bool  # True when the agent hit its step or time budget
    transcript: str


class GitHubPort(Protocol):
    def fetch_issue(self, ref: IssueRef) -> Issue: ...
    def comment(self, ref: IssueRef, body: str) -> None: ...
    def add_label(self, ref: IssueRef, label: str) -> None: ...
    def open_pr(
        self, ref: IssueRef, *, branch: str, title: str, body: str, draft: bool, labels: list[str]
    ) -> str:
        """Return the PR URL."""
        ...


class RunnerPort(Protocol):
    def run(self, request: RunRequest) -> RunResult: ...


class Pipeline:
    """State machine for one issue job: triage, run, review, then PR or escalation."""

    def __init__(
        self,
        *,
        store: Store,
        github: GitHubPort,
        runner: RunnerPort,
        gate: Gate,
        thresholds: Thresholds,
    ) -> None:
        self._store = store
        self._github = github
        self._runner = runner
        self._gate = gate
        self._t = thresholds

    def run(self, ref: IssueRef, job_key: str) -> Job:
        """Run one job. A repeated `job_key` returns the existing job and does nothing."""
        job, created = self._store.begin_job(job_key, ref.url)
        if not created:
            return job
        try:
            self._execute(ref, job_key)
        except JevError as exc:
            self._finish(ref, job_key, JobState.ESCALATED, f"Jev unavailable: {exc}", NEEDS_HUMAN)
        except Exception as exc:  # noqa: BLE001 - any crash must end in a visible failed job
            log.exception("job %s crashed", job_key)
            self._finish(ref, job_key, JobState.FAILED, f"{type(exc).__name__}: {exc}", None)
        final = self._store.get_job(job_key)
        assert final is not None
        return final

    def _execute(self, ref: IssueRef, key: str) -> None:
        issue = self._github.fetch_issue(ref)

        triage = self._gate.triage(issue)
        self._store.log_decision(key, "kind", triage.kind.value.value, triage.kind.confidence)
        self._store.log_decision(
            key, "complexity", triage.complexity.value, triage.complexity.confidence
        )
        self._store.log_decision(key, "fixable", triage.fixable.value, triage.fixable.confidence)
        blocker = triage_blocker(triage, self._t)
        if blocker is not None:
            self._finish(ref, key, JobState.ESCALATED, blocker, NEEDS_HUMAN)
            return
        self._store.set_state(key, JobState.TRIAGED)

        self._store.set_state(key, JobState.RUNNING)
        result = self._runner.run(RunRequest(issue=issue, branch=f"ai-fix/issue-{ref.number}"))
        if not result.diff.strip():
            self._finish(
                ref, key, JobState.ESCALATED, "the agent produced no changes", NEEDS_HUMAN
            )
            return

        review = self._gate.review(issue, result.diff)
        self._store.log_decision(
            key, "addresses_issue", review.addresses_issue.value, review.addresses_issue.confidence
        )
        self._store.log_decision(key, "risk", review.risk.value, review.risk.confidence)
        self._store.set_state(key, JobState.REVIEWED)

        verdict = decide_outcome(
            review=review,
            tests_passed=result.tests_passed,
            incomplete=result.incomplete,
            thresholds=self._t,
        )
        if verdict.outcome is Outcome.ESCALATE:
            self._finish(ref, key, JobState.ESCALATED, verdict.reason, NEEDS_HUMAN)
            return

        pr_url = self._github.open_pr(
            ref,
            branch=result.branch,
            title=f"Fix #{ref.number}: {issue.title}",
            body=_pr_body(ref, result, verdict),
            draft=verdict.outcome is Outcome.DRAFT_PR,
            labels=[INCOMPLETE] if result.incomplete else [],
        )
        self._store.set_state(key, JobState.DONE, pr_url=pr_url, reason=verdict.reason)

    def _finish(
        self, ref: IssueRef, key: str, state: JobState, reason: str, label: str | None
    ) -> None:
        """Record the end state first, then tell the issue. GitHub errors must not undo the record."""
        self._store.set_state(key, state, reason=reason)
        try:
            self._github.comment(ref, _issue_comment(state, reason))
            if label is not None:
                self._github.add_label(ref, label)
        except Exception:  # noqa: BLE001
            log.exception("could not report job %s on %s", key, ref.url)


def _issue_comment(state: JobState, reason: str) -> str:
    if state is JobState.FAILED:
        return f"The agent failed on this issue and made no PR.\n\nReason: {reason}"
    return f"The agent is handing this issue to a human.\n\nReason: {reason}"


def _pr_body(ref: IssueRef, result: RunResult, verdict: Verdict) -> str:
    tests = "passed" if result.tests_passed else "failed"
    return f"Resolves #{ref.number}.\n\nTests: {tests}.\n\nGate: {verdict.reason}."
```

- [ ] **Step 4: Run the full suite and type check**

Run: `cd core && uv run pytest -q && uv run pyright`
Expected: `69 passed`, `0 errors, 0 warnings, 0 informations`

- [ ] **Step 5: Commit**

```bash
git add core
git commit -m "feat(core): add issue-fix pipeline state machine over ports"
```

---

## Self-Review

**Spec coverage (sections this plan owns):**
- Section 2 (Jev is a decision model, never generates): Tasks 2-4 use Jev only for typed questions.
- Section 3 `jev_gate`, `pipeline`, `store`, `config`: Tasks 1-7. `runner` and `github` are ports here, built in Plan 2.
- Section 4 steps 1-5 and thresholds in one typed config, every answer logged: Tasks 1, 6, 7.
- Section 6: Jev failure escalates (Tasks 3, 7), budget exceeded gives labeled draft PR (Task 7), sandbox crash gives `failed` plus comment (Task 7), replay is idempotent (Tasks 5, 7). Allowlist and "never push to default branch" belong to Plan 2's real adapters.
- Section 7: `Decision[T]` plus `gate()` (Task 2). OpenAPI and TS drift check belong to Plans 2 and 3.
- Not in this plan by design: watchdog (Plan 4), gateway (Plan 3), CLI and FastAPI (Plan 2), evals (after Plan 2).

**Placeholder scan:** none. Every code step shows full file contents; every run step names a command and expected output.

**Type consistency:** `Gate.triage/review`, `Triage`, `Review`, `Decision`, `RunRequest`, `RunResult`, `GitHubPort`, `Store` method names and `JobState` members match across Tasks 2-7 (verified by `pyright` strict and 69 passing tests when the files are assembled in task order).
