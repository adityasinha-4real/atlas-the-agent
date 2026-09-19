"""Frozen event contracts (design doc §10: contract-first, stable).

An ``Event`` is the atomic unit of the run ledger. Every event carries a
monotonically increasing ``seq`` (per run) so clients can backfill and replay
deterministically. Payloads are typed loosely as ``dict`` here; each event type
documents its payload shape. New event types are ADDED over milestones; existing
members and the envelope shape do not change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


def _utcnow() -> datetime:
    return datetime.now(UTC)


class EventType(StrEnum):
    """Canonical event type names.

    Grouped by lifecycle stage. Milestone in which each is first emitted is
    noted so later milestones extend rather than rewrite this enum.
    """

    # --- Run lifecycle (M1) ---
    RUN_CREATED = "run.created"
    RUN_STARTED = "run.started"
    RUN_COMPLETED = "run.completed"
    RUN_FAILED = "run.failed"
    RUN_CANCELLED = "run.cancelled"

    # --- Generation stream (M1) ---
    ANSWER_TOKEN = "answer.token"
    ANSWER_COMPLETED = "answer.completed"

    # --- Diagnostics (M1) ---
    LOG = "log"

    # --- Reserved for later milestones (declared early, emitted later) ---
    # M2: tool loop
    THOUGHT = "thought"
    TOOL_CALL = "tool.call"
    TOOL_RESULT = "tool.result"
    # M3: planning
    PLAN_CREATED = "plan.created"
    TASK_STARTED = "task.started"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    # M4: reflection / self-correction (RFC-0002). The former reserved
    # REFLECTION/REPLAN placeholders (never emitted or persisted) are renamed to
    # the subject.verb convention and joined by four more.
    TASK_REFLECTED = "task.reflected"
    TASK_RETRYING = "task.retrying"
    TASK_SKIPPED = "task.skipped"
    TASK_CANCELLED = "task.cancelled"
    PLAN_REPLANNED = "plan.replanned"
    BUDGET_EXCEEDED = "budget.exceeded"
    # M5: memory (RFC-0003). ``memory.recalled`` was reserved in M4 and is first
    # emitted here; ``memory.written`` is added.
    MEMORY_RECALLED = "memory.recalled"
    MEMORY_WRITTEN = "memory.written"


class Event(BaseModel):
    """A single, persisted, replayable event in a run's ledger."""

    run_id: str
    seq: int = Field(ge=1, description="Monotonic per-run sequence, starts at 1.")
    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: datetime = Field(default_factory=_utcnow)

    model_config = {"frozen": True}


class EventCreate(BaseModel):
    """Input to ``emit()``. ``seq`` and ``ts`` are assigned by the emitter."""

    type: EventType
    payload: dict[str, Any] = Field(default_factory=dict)
