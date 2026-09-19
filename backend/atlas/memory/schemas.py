"""Frozen memory contracts (RFC-0003 §5).

``MemoryRecord`` is the write input (what a finished run distills into);
``MemoryView`` is the persisted/serialized shape; ``RecalledMemory`` wraps a view
with the score and the (possibly truncated) lesson selected for injection.
``RecallResult`` bundles a recall's selected memories with the exact rendered
block, which is what the ``memory.recalled`` event carries for replay fidelity
(invariant I-19).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class MemoryOutcome(StrEnum):
    """How the run a memory was distilled from ended."""

    DONE = "done"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class MemorySource(StrEnum):
    """How a memory's summary/lessons were produced."""

    SYNTHESIZED = "synthesized"  # distilled by an LLM call
    HEURISTIC = "heuristic"  # zero-LLM fallback


class MemoryStatus(StrEnum):
    """Lifecycle status; ``expired`` is a soft-delete awaiting hard prune."""

    ACTIVE = "active"
    EXPIRED = "expired"


class MemoryRecord(BaseModel):
    """A distilled memory ready to persist (RFC-0003 §5/§6 create).

    Only the goal and model-distilled ``summary``/``lessons`` are carried — never
    raw tool output or fetched content (invariant I-18).
    """

    run_id: str | None = None
    goal: str
    outcome: MemoryOutcome
    summary: str = ""
    lessons: str = ""
    tools_used: list[str] = Field(default_factory=list)
    task_count: int = 0
    source: MemorySource = MemorySource.SYNTHESIZED

    model_config = {"frozen": True, "extra": "ignore"}


class MemoryView(BaseModel):
    """A persisted memory as returned by the store and the API."""

    id: str
    run_id: str | None = None
    goal: str
    outcome: MemoryOutcome
    success: bool = False
    summary: str = ""
    lessons: str = ""
    tools_used: list[str] = Field(default_factory=list)
    task_count: int = 0
    source: MemorySource = MemorySource.SYNTHESIZED
    salience: float = 1.0
    use_count: int = 0
    last_recalled_at: datetime | None = None
    pinned: bool = False
    status: MemoryStatus = MemoryStatus.ACTIVE
    created_at: datetime
    updated_at: datetime


class RecalledMemory(BaseModel):
    """A memory selected for injection, with its score and rendered lesson."""

    memory: MemoryView
    score: float
    lesson: str  # the (possibly truncated) lesson text used in the block

    model_config = {"frozen": True}


class RecallResult(BaseModel):
    """The outcome of a plan-time recall (RFC-0003 §7)."""

    memories: list[RecalledMemory] = Field(default_factory=list)
    rendered: str = ""

    model_config = {"frozen": True}

    @property
    def memory_ids(self) -> list[str]:
        return [m.memory.id for m in self.memories]

    @property
    def is_empty(self) -> bool:
        return not self.memories

    @classmethod
    def empty(cls) -> RecallResult:
        return cls()
