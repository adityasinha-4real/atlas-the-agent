"""SQLAlchemy ORM models.

M1 defined two of the four V2 tables: ``runs`` and ``events``. M3 adds ``tasks``;
M4 adds ``task_attempts``; ``memories`` arrives in M5. All live behind the same
declarative base, so adding a table is additive — ``Base.metadata.create_all``
creates it without touching existing rows. New *columns* on an existing table
(M4's additions to ``tasks``) are handled by ``database._apply_light_migrations``
since ``create_all`` never alters a table that already exists.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


def _utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    """Declarative base for all ATLAS ORM models."""


class RunRow(Base):
    """A single agent run — the persisted ledger head."""

    __tablename__ = "runs"
    # ``GET /runs`` lists newest-first; without this index SQLite does a full table
    # SCAN + temp B-tree sort (M6/RFC-0004 §7/§9 benchmark). Indexing ``created_at``
    # lets it read the index in reverse and LIMIT without sorting. Additive.
    __table_args__ = (Index("ix_runs_created", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    events: Mapped[list[EventRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EventRow.seq",
    )
    tasks: Mapped[list[TaskRow]] = relationship(
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="TaskRow.index",
    )


class EventRow(Base):
    """An immutable event in a run's ledger.

    ``(run_id, seq)`` is unique and monotonically increasing per run, which makes
    backfill (``after=seq``) and replay deterministic.
    """

    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("run_id", "seq", name="uq_events_run_seq"),
        Index("ix_events_run_seq", "run_id", "seq"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    type: Mapped[str] = mapped_column(String(48), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    run: Mapped[RunRow] = relationship(back_populates="events")


class TaskRow(Base):
    """A single planned task within a run's ordered task list (design doc §4).

    ``(run_id, index)`` is unique and defines execution order. ``success_criteria``
    is stored now but first consumed by the M4 reflector; ``suggested_tool`` is an
    advisory hint validated against the tool registry at plan time.

    M4 (RFC-0002) adds ``attempt_count`` (denormalized latest attempt),
    ``replan_generation`` (which plan generation this task belongs to), and
    ``parent_generation`` (the generation a replan descended from; NULL for the
    original plan). These are populated by the reflect/retry/replan loop in later
    M4 phases; here they exist with safe defaults.
    """

    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("run_id", "index", name="uq_tasks_run_index"),
        Index("ix_tasks_run_index", "run_id", "index"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    index: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    success_criteria: Mapped[str] = mapped_column(Text, nullable=False, default="")
    suggested_tool: Mapped[str | None] = mapped_column(String(48), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    replan_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_generation: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )

    run: Mapped[RunRow] = relationship(back_populates="tasks")
    attempts: Mapped[list[TaskAttemptRow]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskAttemptRow.attempt_number",
    )


class TaskAttemptRow(Base):
    """One persisted attempt of a task and its reflection (RFC-0002 §11.1).

    Preserves every attempt (retry history) and the reflection verdict that
    judged it. ``attempt_id`` is a stable opaque key ``{task_id}#{attempt_number}``;
    ``attempt_number`` is the 1-based ordinal. Reflection columns are NULL until
    the attempt has been judged. Cascade-deleted with its task or run.
    """

    __tablename__ = "task_attempts"
    __table_args__ = (
        UniqueConstraint(
            "task_id", "attempt_number", name="uq_attempts_task_number"
        ),
        Index("ix_attempts_task_number", "task_id", "attempt_number"),
    )

    attempt_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_id: Mapped[str] = mapped_column(
        String(48), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False
    )
    run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="CASCADE"), nullable=False
    )
    attempt_number: Mapped[int] = mapped_column(Integer, nullable=False)
    output: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reflection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    reflection_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    reflection_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reflection_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    task: Mapped[TaskRow] = relationship(back_populates="attempts")


class SchemaMetaRow(Base):
    """Tiny key/value stamp of the on-disk schema and app version (M6, §14).

    Additive and downgrade-safe (invariant I-27): older code simply ignores the
    table. Recovery and integrity checks read it to reason about on-disk shape.
    """

    __tablename__ = "schema_meta"

    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)


class MemoryRow(Base):
    """A distilled episodic memory of one finished run (RFC-0003 §5).

    ``run_id`` is provenance only and is nullable with ``ON DELETE SET NULL`` — a
    lesson survives deleting the run it came from (unlike ``events``/``tasks``,
    which cascade). ``UNIQUE(run_id)`` makes writing idempotent per run (one memory
    per run; a re-finalized run upserts rather than duplicates — invariant I-17).
    SQLite treats NULL run_ids as distinct, so several run-less memories may
    coexist. Only ``goal``/``summary``/``lessons`` hold text (indexed by the
    ``memories_fts`` FTS5 table); no raw tool output is stored (invariant I-18).
    """

    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("run_id", name="uq_memories_run"),
        Index("ix_memories_status_created", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(48), primary_key=True)
    run_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("runs.id", ondelete="SET NULL"), nullable=True
    )
    goal: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False, default="")
    lessons: Mapped[str] = mapped_column(Text, nullable=False, default="")
    tools_used: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    task_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    salience: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    use_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_recalled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    pinned: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="active", index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )
