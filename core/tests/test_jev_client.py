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
