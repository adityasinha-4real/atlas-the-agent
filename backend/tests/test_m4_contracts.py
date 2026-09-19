"""M4 Phase 1 contracts (RFC-0002): reflection/attempt schemas, FSM + event enum.

These are pure contract declarations wired up by later M4 phases; here we lock
their shape, defaults, and invariants so the rest of M4 builds on a stable base.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from atlas.agent.schemas import (
    ReflectionDecision,
    ReflectionResult,
    ReflectionSource,
    TaskAttemptView,
    TaskStatus,
)
from atlas.events.types import EventType

# --- Enums ----------------------------------------------------------------- #


def test_reflection_decision_members() -> None:
    assert {d.value for d in ReflectionDecision} == {
        "accept",
        "retry",
        "replan",
        "abort",
    }


def test_reflection_source_members() -> None:
    assert {s.value for s in ReflectionSource} == {"precheck", "llm", "degraded"}


def test_task_status_gains_m4_states() -> None:
    assert TaskStatus.RETRYING == "retrying"
    assert TaskStatus.CANCELLED == "cancelled"
    # M3 states remain unchanged.
    assert TaskStatus.PENDING == "pending"
    assert TaskStatus.SKIPPED == "skipped"


def test_event_enum_renames_and_additions() -> None:
    # Reserved placeholders were renamed to the subject.verb convention.
    assert EventType.TASK_REFLECTED == "task.reflected"
    assert EventType.PLAN_REPLANNED == "plan.replanned"
    # Four new events.
    assert EventType.TASK_RETRYING == "task.retrying"
    assert EventType.TASK_SKIPPED == "task.skipped"
    assert EventType.TASK_CANCELLED == "task.cancelled"
    assert EventType.BUDGET_EXCEEDED == "budget.exceeded"
    # The old bare names no longer exist.
    assert not hasattr(EventType, "REFLECTION")
    assert not hasattr(EventType, "REPLAN")


# --- ReflectionResult ------------------------------------------------------ #


def test_reflection_result_defaults() -> None:
    result = ReflectionResult(decision=ReflectionDecision.ACCEPT)
    assert result.reason == ""
    assert result.confidence == 0.5
    assert result.source is ReflectionSource.LLM
    assert result.reflection_version == 1


def test_reflection_result_accepts_string_decision() -> None:
    result = ReflectionResult(decision="retry")  # type: ignore[arg-type]
    assert result.decision is ReflectionDecision.RETRY


def test_reflection_result_is_frozen() -> None:
    result = ReflectionResult(decision=ReflectionDecision.ACCEPT)
    with pytest.raises(ValidationError):
        result.decision = ReflectionDecision.ABORT  # type: ignore[misc]


@pytest.mark.parametrize("bad", [1.5, -0.1])
def test_reflection_result_confidence_bounds(bad: float) -> None:
    with pytest.raises(ValidationError):
        ReflectionResult(decision=ReflectionDecision.ACCEPT, confidence=bad)


def test_reflection_result_version_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ReflectionResult(decision=ReflectionDecision.ACCEPT, reflection_version=0)


# --- TaskAttemptView ------------------------------------------------------- #


def test_task_attempt_view_reflection_fields_default_null() -> None:
    view = TaskAttemptView(
        attempt_id="run1:0#1",
        task_id="run1:0",
        run_id="run1",
        attempt_number=1,
        created_at=datetime.now(UTC),
    )
    assert view.output is None
    assert view.reflection_decision is None
    assert view.reflection_confidence is None
    assert view.reflection_source is None
    assert view.reflection_version is None


def test_task_attempt_view_carries_reflection() -> None:
    view = TaskAttemptView(
        attempt_id="run1:0#2",
        task_id="run1:0",
        run_id="run1",
        attempt_number=2,
        output="result",
        reflection_decision=ReflectionDecision.ACCEPT,
        reflection_reason="meets criteria",
        reflection_confidence=0.8,
        reflection_source=ReflectionSource.LLM,
        reflection_version=1,
        created_at=datetime.now(UTC),
    )
    assert view.reflection_decision is ReflectionDecision.ACCEPT
    assert view.attempt_number == 2
