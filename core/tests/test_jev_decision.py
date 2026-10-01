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
