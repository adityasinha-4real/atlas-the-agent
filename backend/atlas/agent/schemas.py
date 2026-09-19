"""Frozen agent/domain contracts (design doc §10).

These Pydantic models are the stable public shapes used by the runtime, the API,
and the generated frontend client. Enums declare the FULL V2 state machines now
(even though M1 only exercises a subset) so later milestones extend behavior
without changing contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunStatus(StrEnum):
    """Run-level FSM (design doc §4): 6 states.

    CREATED → PLANNING → RUNNING → DONE, with terminal FAILED / CANCELLED and a
    RUNNING ⇄ PAUSED loop. M1 uses CREATED → RUNNING → DONE/FAILED/CANCELLED.
    """

    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    PAUSED = "paused"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_RUN_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.DONE, RunStatus.FAILED, RunStatus.CANCELLED}
)


class TaskStatus(StrEnum):
    """Task-level FSM (design doc §4). Declared now; exercised from M3.

    M4 (RFC-0002) exercises ``RETRYING`` (a task awaiting its next attempt after
    a ``retry`` verdict) and ``CANCELLED`` (a non-terminal task stopped by run
    cancellation).
    """

    PENDING = "pending"
    RUNNING = "running"
    RETRYING = "retrying"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"
    CANCELLED = "cancelled"


# --------------------------------------------------------------------------- #
# API request / response contracts
# --------------------------------------------------------------------------- #


class CreateRunRequest(BaseModel):
    """Payload for ``POST /runs``."""

    goal: str = Field(min_length=1, max_length=4000)

    @field_validator("goal")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("goal must not be empty")
        return stripped


class RunView(BaseModel):
    """Serialized run as returned by the API.

    ``partial`` (M4) is a derived read-only flag: True when a run finalized
    ``FAILED`` yet still carries a synthesized answer — i.e. a graceful abort
    produced a best-effort partial answer (RFC-0002 ADR-0014). Additive; defaults
    to False so existing consumers are unaffected.
    """

    id: str
    goal: str
    status: RunStatus
    answer: str | None = None
    error: str | None = None
    partial: bool = False
    created_at: datetime
    updated_at: datetime


class RunSummary(BaseModel):
    """Compact run row for the History list (``GET /runs``)."""

    id: str
    goal: str
    status: RunStatus
    created_at: datetime


class HealthView(BaseModel):
    """Payload for ``GET /health``."""

    status: str = "ok"
    app: str
    version: str
    llm_provider: str
    time: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------- #
# Planning contracts (M3)
# --------------------------------------------------------------------------- #


class PlannedTask(BaseModel):
    """One task as produced by the planner (before persistence).

    Parsed tolerantly from model JSON: fields default to empty so a partially
    malformed item still validates and the planner can apply business rules
    (drop empty descriptions, null unknown tools) rather than raising here.
    """

    description: str = ""
    success_criteria: str = ""
    suggested_tool: str | None = None

    model_config = {"extra": "ignore"}


class Plan(BaseModel):
    """The planner's top-level output: an ordered list of tasks."""

    tasks: list[PlannedTask] = Field(default_factory=list)

    model_config = {"extra": "ignore"}


class TaskView(BaseModel):
    """A persisted task as returned by the API and carried through the runtime."""

    id: str
    run_id: str
    index: int
    description: str
    success_criteria: str = ""
    suggested_tool: str | None = None
    status: TaskStatus
    output: str | None = None
    error: str | None = None
    # Self-correction projection (M4, RFC-0002 §11.2). All additive with safe
    # defaults so pre-M4 consumers and stored rows are unaffected.
    attempt_count: int = 0
    replan_generation: int = 0
    parent_generation: int | None = None
    created_at: datetime
    updated_at: datetime


class PriorTaskOutput(BaseModel):
    """A completed upstream task's output, as fed into a later task's context."""

    index: int
    description: str
    output: str


class TaskContext(BaseModel):
    """Typed bundle passed from the runtime to the Context Builder.

    This is the stable seam between planning and execution: the runtime assembles
    the goal, the current task, and truncated prior-task outputs; the Context
    Builder turns it into executor messages. Later milestones (memory recall in
    M5, replanning in M4, multi-agent execution) extend this object additively
    rather than changing the executor's input construction each time.
    """

    goal: str
    task: TaskView
    prior_outputs: list[PriorTaskOutput] = Field(default_factory=list)
    total_tasks: int = 1
    # Self-correction (M4): the current attempt number and, on a retry
    # (``attempt`` > 1), the previous attempt's output and why it was judged
    # insufficient — the Context Builder renders these into a critique block.
    attempt: int = 1
    previous_output: str | None = None
    critique_reason: str | None = None


# --------------------------------------------------------------------------- #
# Self-correction contracts (M4, RFC-0002)
# --------------------------------------------------------------------------- #


class ReflectionDecision(StrEnum):
    """The Reflector's verdict on a completed task attempt (RFC-0002 §7)."""

    ACCEPT = "accept"
    RETRY = "retry"
    REPLAN = "replan"
    ABORT = "abort"


class ReflectionSource(StrEnum):
    """How a ``ReflectionResult`` was produced (for auditing and metrics)."""

    PRECHECK = "precheck"  # deterministic gate, no LLM call
    LLM = "llm"  # parsed from the reflector model
    DEGRADED = "degraded"  # unparseable after repair -> acceptance bias


class ReflectionResult(BaseModel):
    """A validated reflection verdict — a frozen value object (RFC-0002 §7.3).

    Carries no reference to run/task state, which keeps the Reflector completely
    stateless (RFC-0002 Amendment 10 / invariant I-7). ``reflection_version``
    records the prompt/parse version in effect when the verdict was made so
    historical reflections stay interpretable after a prompt revision.
    """

    decision: ReflectionDecision
    reason: str = ""
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: ReflectionSource = ReflectionSource.LLM
    reflection_version: int = Field(default=1, ge=1)

    model_config = {"frozen": True, "extra": "ignore"}


class TaskAttemptView(BaseModel):
    """One persisted task attempt and its reflection (RFC-0002 §11.1).

    ``attempt_id`` is a stable opaque key (safe to reference); ``attempt_number``
    is the 1-based ordinal used for ordering. Reflection fields are null until
    the attempt has been judged.
    """

    attempt_id: str
    task_id: str
    run_id: str
    attempt_number: int
    output: str | None = None
    error: str | None = None
    reflection_decision: ReflectionDecision | None = None
    reflection_reason: str | None = None
    reflection_confidence: float | None = None
    reflection_source: ReflectionSource | None = None
    reflection_version: int | None = None
    created_at: datetime
