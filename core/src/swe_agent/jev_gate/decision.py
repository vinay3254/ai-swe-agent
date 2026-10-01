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
