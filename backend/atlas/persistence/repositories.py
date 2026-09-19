"""Repository pattern over the ORM.

Keeping data access behind repositories is one of the V2 "seams to build now":
it makes the eventual SQLite→Postgres migration a bounded change and keeps the
runtime free of SQL. All methods take an ``AsyncSession`` supplied by the caller
so a unit of work can span multiple repositories.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select, text, update
from sqlalchemy.exc import OperationalError

from atlas.agent.schemas import (
    TERMINAL_RUN_STATUSES,
    PlannedTask,
    ReflectionDecision,
    ReflectionResult,
    ReflectionSource,
    RunStatus,
    RunSummary,
    RunView,
    TaskAttemptView,
    TaskStatus,
    TaskView,
)
from atlas.events.types import Event, EventType
from atlas.memory.schemas import (
    MemoryOutcome,
    MemoryRecord,
    MemorySource,
    MemoryStatus,
    MemoryView,
)
from atlas.persistence.models import (
    EventRow,
    MemoryRow,
    RunRow,
    TaskAttemptRow,
    TaskRow,
)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class RunRepository:
    """CRUD + projection updates for runs."""

    def __init__(self, session) -> None:  # noqa: ANN001 - AsyncSession
        self._session = session

    async def create(self, run_id: str, goal: str) -> RunRow:
        row = RunRow(id=run_id, goal=goal, status=RunStatus.CREATED.value)
        self._session.add(row)
        await self._session.flush()
        return row

    async def get(self, run_id: str) -> RunRow | None:
        return await self._session.get(RunRow, run_id)

    async def list_recent(self, limit: int = 50) -> list[RunSummary]:
        stmt = select(RunRow).order_by(RunRow.created_at.desc()).limit(limit)
        rows = (await self._session.execute(stmt)).scalars().all()
        return [
            RunSummary(
                id=r.id,
                goal=r.goal,
                status=RunStatus(r.status),
                created_at=r.created_at,
            )
            for r in rows
        ]

    async def list_non_terminal(self) -> list[RunRow]:
        """All runs not in a terminal state — i.e. interrupted after a crash (M6).

        After a process restart no run has a live orchestrating task, so every
        non-terminal row is a candidate for reconciliation (RFC-0004 §13).
        """
        terminal = [s.value for s in TERMINAL_RUN_STATUSES]
        stmt = select(RunRow).where(RunRow.status.notin_(terminal))
        return list((await self._session.execute(stmt)).scalars().all())

    async def set_status(self, run_id: str, status: RunStatus) -> None:
        row = await self._session.get(RunRow, run_id)
        if row is not None:
            row.status = status.value
            row.updated_at = _utcnow()

    async def set_answer(self, run_id: str, answer: str) -> None:
        row = await self._session.get(RunRow, run_id)
        if row is not None:
            row.answer = answer
            row.updated_at = _utcnow()

    async def set_error(self, run_id: str, error: str) -> None:
        row = await self._session.get(RunRow, run_id)
        if row is not None:
            row.error = error
            row.updated_at = _utcnow()

    @staticmethod
    def to_view(row: RunRow) -> RunView:
        status = RunStatus(row.status)
        return RunView(
            id=row.id,
            goal=row.goal,
            status=status,
            answer=row.answer,
            error=row.error,
            # A FAILED run carrying an answer is a graceful-abort partial (ADR-0014).
            partial=status is RunStatus.FAILED and row.answer is not None,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class TaskRepository:
    """CRUD + status transitions for a run's ordered task list (M3)."""

    def __init__(self, session) -> None:  # noqa: ANN001 - AsyncSession
        self._session = session

    async def bulk_create(
        self, run_id: str, planned: list[PlannedTask]
    ) -> list[TaskView]:
        """Persist an ordered plan as ``PENDING`` tasks; return their views."""
        rows: list[TaskRow] = []
        for index, task in enumerate(planned):
            row = TaskRow(
                id=f"{run_id}:{index}",
                run_id=run_id,
                index=index,
                description=task.description,
                success_criteria=task.success_criteria,
                suggested_tool=task.suggested_tool,
                status=TaskStatus.PENDING.value,
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return [self.to_view(r) for r in rows]

    async def append_generation(
        self,
        run_id: str,
        planned: list[PlannedTask],
        *,
        start_index: int,
        generation: int,
        parent_generation: int | None,
    ) -> list[TaskView]:
        """Persist a replan's tasks as a new generation (RFC-0002 §11.2).

        Indices continue monotonically from ``start_index`` so ``UNIQUE(run_id,
        index)`` holds and the ``{run_id}:{index}`` id scheme is preserved without
        rewriting existing rows. ``generation``/``parent_generation`` record the
        replan lineage (Amendment 9).
        """
        rows: list[TaskRow] = []
        for offset, task in enumerate(planned):
            index = start_index + offset
            row = TaskRow(
                id=f"{run_id}:{index}",
                run_id=run_id,
                index=index,
                description=task.description,
                success_criteria=task.success_criteria,
                suggested_tool=task.suggested_tool,
                status=TaskStatus.PENDING.value,
                replan_generation=generation,
                parent_generation=parent_generation,
            )
            self._session.add(row)
            rows.append(row)
        await self._session.flush()
        return [self.to_view(r) for r in rows]

    async def list_for_run(self, run_id: str) -> list[TaskView]:
        stmt = (
            select(TaskRow)
            .where(TaskRow.run_id == run_id)
            .order_by(TaskRow.index.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self.to_view(r) for r in rows]

    async def mark_running(self, task_id: str) -> None:
        await self._set(task_id, status=TaskStatus.RUNNING)

    async def mark_done(self, task_id: str, output: str) -> None:
        await self._set(task_id, status=TaskStatus.DONE, output=output)

    async def mark_failed(self, task_id: str, error: str) -> None:
        await self._set(task_id, status=TaskStatus.FAILED, error=error)

    # -- M4 transitions (RFC-0002). Wired into the runtime in later M4 phases. --

    async def mark_retrying(self, task_id: str) -> None:
        """Task judged ``retry`` and awaiting its next attempt."""
        await self._set(task_id, status=TaskStatus.RETRYING)

    async def mark_cancelled(self, task_id: str) -> None:
        """A non-terminal task stopped by run cancellation."""
        await self._set(task_id, status=TaskStatus.CANCELLED)

    async def mark_skipped(self, task_id: str) -> None:
        """A pending task dropped by a replan or an abort."""
        await self._set(task_id, status=TaskStatus.SKIPPED)

    async def bulk_skip(self, task_ids: Sequence[str]) -> None:
        """Mark several tasks ``SKIPPED`` in one statement (replan/abort)."""
        if not task_ids:
            return
        await self._session.execute(
            update(TaskRow)
            .where(TaskRow.id.in_(list(task_ids)))
            .values(status=TaskStatus.SKIPPED.value, updated_at=_utcnow())
        )

    async def set_generation(
        self, task_id: str, *, generation: int, parent_generation: int | None
    ) -> None:
        """Record which plan generation a (replanned) task belongs to."""
        row = await self._session.get(TaskRow, task_id)
        if row is None:
            return
        row.replan_generation = generation
        row.parent_generation = parent_generation
        row.updated_at = _utcnow()

    async def set_attempt_count(self, task_id: str, count: int) -> None:
        """Update the denormalized latest-attempt counter (M4)."""
        row = await self._session.get(TaskRow, task_id)
        if row is None:
            return
        row.attempt_count = count
        row.updated_at = _utcnow()

    async def _set(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        output: str | None = None,
        error: str | None = None,
    ) -> None:
        row = await self._session.get(TaskRow, task_id)
        if row is None:
            return
        row.status = status.value
        if output is not None:
            row.output = output
        if error is not None:
            row.error = error
        row.updated_at = _utcnow()

    @staticmethod
    def to_view(row: TaskRow) -> TaskView:
        return TaskView(
            id=row.id,
            run_id=row.run_id,
            index=row.index,
            description=row.description,
            success_criteria=row.success_criteria,
            suggested_tool=row.suggested_tool,
            status=TaskStatus(row.status),
            output=row.output,
            error=row.error,
            attempt_count=row.attempt_count,
            replan_generation=row.replan_generation,
            parent_generation=row.parent_generation,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )


class TaskAttemptRepository:
    """Create and read per-task attempt rows and their reflections (M4).

    An attempt row is created once its executor output (or error) is known; the
    reflection verdict is recorded onto the same row afterwards via
    ``record_reflection``. The runtime wiring lands in later M4 phases; the
    persistence primitives live here.
    """

    def __init__(self, session) -> None:  # noqa: ANN001 - AsyncSession
        self._session = session

    async def create(
        self,
        *,
        task_id: str,
        run_id: str,
        attempt_number: int,
        output: str | None = None,
        error: str | None = None,
    ) -> TaskAttemptView:
        """Persist one attempt (before it is judged) and return its view."""
        row = TaskAttemptRow(
            attempt_id=f"{task_id}#{attempt_number}",
            task_id=task_id,
            run_id=run_id,
            attempt_number=attempt_number,
            output=output,
            error=error,
        )
        self._session.add(row)
        await self._session.flush()
        return self.to_view(row)

    async def record_reflection(
        self, attempt_id: str, result: ReflectionResult
    ) -> None:
        """Attach a reflection verdict to a previously-created attempt row."""
        row = await self._session.get(TaskAttemptRow, attempt_id)
        if row is None:
            return
        row.reflection_decision = result.decision.value
        row.reflection_reason = result.reason
        row.reflection_confidence = result.confidence
        row.reflection_source = result.source.value
        row.reflection_version = result.reflection_version

    async def list_for_task(self, task_id: str) -> list[TaskAttemptView]:
        stmt = (
            select(TaskAttemptRow)
            .where(TaskAttemptRow.task_id == task_id)
            .order_by(TaskAttemptRow.attempt_number.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self.to_view(r) for r in rows]

    @staticmethod
    def to_view(row: TaskAttemptRow) -> TaskAttemptView:
        return TaskAttemptView(
            attempt_id=row.attempt_id,
            task_id=row.task_id,
            run_id=row.run_id,
            attempt_number=row.attempt_number,
            output=row.output,
            error=row.error,
            reflection_decision=(
                ReflectionDecision(row.reflection_decision)
                if row.reflection_decision is not None
                else None
            ),
            reflection_reason=row.reflection_reason,
            reflection_confidence=row.reflection_confidence,
            reflection_source=(
                ReflectionSource(row.reflection_source)
                if row.reflection_source is not None
                else None
            ),
            reflection_version=row.reflection_version,
            created_at=row.created_at,
        )


class EventRepository:
    """Append-only writes and ordered reads for the event ledger."""

    def __init__(self, session) -> None:  # noqa: ANN001 - AsyncSession
        self._session = session

    async def next_seq(self, run_id: str) -> int:
        """Return the next per-run sequence number (1-based)."""
        stmt = select(func.max(EventRow.seq)).where(EventRow.run_id == run_id)
        current = (await self._session.execute(stmt)).scalar_one_or_none()
        return (current or 0) + 1

    async def append(
        self, run_id: str, seq: int, type_: EventType, payload: dict
    ) -> Event:
        row = EventRow(run_id=run_id, seq=seq, type=type_.value, payload=payload)
        self._session.add(row)
        await self._session.flush()
        return self._to_event(row)

    async def list_after(self, run_id: str, after_seq: int = 0) -> list[Event]:
        stmt = (
            select(EventRow)
            .where(EventRow.run_id == run_id, EventRow.seq > after_seq)
            .order_by(EventRow.seq.asc())
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self._to_event(r) for r in rows]

    @staticmethod
    def _to_event(row: EventRow) -> Event:
        return Event(
            run_id=row.run_id,
            seq=row.seq,
            type=EventType(row.type),
            payload=row.payload,
            ts=row.ts,
        )


_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _memory_terms(value: str) -> list[str]:
    """Lowercased alphanumeric tokens (length ≥ 2) used for search/matching."""
    return [tok for tok in _TOKEN_RE.findall(value.lower()) if len(tok) >= 2]


def _fts_match_query(terms: Sequence[str]) -> str:
    """A safe FTS5 MATCH string: each distinct term quoted and OR-joined.

    Quoting each token avoids FTS5 syntax injection from arbitrary goal text.
    """
    return " OR ".join(f'"{tok}"' for tok in dict.fromkeys(terms))


def _retention_key(row: MemoryRow, now: datetime) -> float:
    """Prune ordering: lower = evicted first (low salience and/or stale).

    Uses POSIX timestamps so a naive datetime read back from SQLite and an
    aware ``now`` do not raise on subtraction.
    """
    last = row.last_recalled_at or row.created_at
    age_days = max(0.0, (now.timestamp() - last.timestamp()) / 86400.0)
    return row.salience - 0.01 * age_days


class MemoryRepository:
    """CRUD, full-text search, and prune for episodic memories (RFC-0003).

    Search prefers SQLite FTS5 (``memories_fts``); if the build lacks FTS5 the
    query falls back to a ``LIKE`` scan (ADR-0017). Ranking/selection of the
    retrieved candidates lives in :mod:`atlas.memory.ranking`, not here — this
    layer only persists and retrieves.
    """

    def __init__(self, session) -> None:  # noqa: ANN001 - AsyncSession
        self._session = session

    async def create(self, record: MemoryRecord) -> MemoryView:
        """Insert a memory, or upsert the existing one for ``record.run_id``.

        Upserting on ``run_id`` keeps writing idempotent per run (invariant I-17):
        a re-finalized run refreshes its memory's content while preserving the
        learned ranking signals (``salience``/``use_count``/``pinned``).
        """
        now = _utcnow()
        row: MemoryRow | None = None
        if record.run_id is not None:
            row = (
                await self._session.execute(
                    select(MemoryRow).where(MemoryRow.run_id == record.run_id)
                )
            ).scalar_one_or_none()
        if row is None:
            row = MemoryRow(
                id=f"mem_{uuid.uuid4().hex[:24]}",
                run_id=record.run_id,
                created_at=now,
            )
            self._session.add(row)
        row.goal = record.goal
        row.outcome = record.outcome.value
        row.success = record.outcome is MemoryOutcome.DONE
        row.summary = record.summary
        row.lessons = record.lessons
        row.tools_used = list(record.tools_used)
        row.task_count = record.task_count
        row.source = record.source.value
        row.status = MemoryStatus.ACTIVE.value
        row.updated_at = now
        await self._session.flush()
        return self.to_view(row)

    async def get(self, memory_id: str) -> MemoryView | None:
        row = await self._session.get(MemoryRow, memory_id)
        return self.to_view(row) if row is not None else None

    async def list_recent(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: MemoryStatus = MemoryStatus.ACTIVE,
    ) -> list[MemoryView]:
        stmt = (
            select(MemoryRow)
            # id is the secondary key so pagination is stable when several rows
            # share a created_at timestamp.
            .where(MemoryRow.status == status.value)
            .order_by(MemoryRow.created_at.desc(), MemoryRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self.to_view(r) for r in rows]

    async def search(self, goal: str, *, limit: int) -> list[MemoryView]:
        """Return up to ``limit`` active candidate memories relevant to ``goal``.

        Retrieval only — FTS5 (bm25-ordered) with a ``LIKE`` fallback. Final
        relevance scoring is the ranking layer's job.
        """
        terms = _memory_terms(goal)
        if not terms or limit <= 0:
            return []
        try:
            stmt = text(
                "SELECT m.id AS id FROM memories_fts "
                "JOIN memories m ON m.rowid = memories_fts.rowid "
                "WHERE memories_fts MATCH :q AND m.status = :status "
                "ORDER BY bm25(memories_fts) LIMIT :limit"
            )
            result = await self._session.execute(
                stmt,
                {
                    "q": _fts_match_query(terms),
                    "status": MemoryStatus.ACTIVE.value,
                    "limit": limit,
                },
            )
            ranked_ids = [r[0] for r in result.all()]
        except OperationalError:
            return await self._like_search(terms, limit=limit)

        if not ranked_ids:
            return []
        rows = (
            await self._session.execute(
                select(MemoryRow).where(MemoryRow.id.in_(ranked_ids))
            )
        ).scalars().all()
        by_id = {r.id: r for r in rows}
        return [self.to_view(by_id[i]) for i in ranked_ids if i in by_id]

    async def _like_search(
        self, terms: Sequence[str], *, limit: int
    ) -> list[MemoryView]:
        conditions = []
        for tok in terms:
            like = f"%{tok}%"
            conditions.append(func.lower(MemoryRow.goal).like(like))
            conditions.append(func.lower(MemoryRow.summary).like(like))
            conditions.append(func.lower(MemoryRow.lessons).like(like))
        stmt = (
            select(MemoryRow)
            .where(MemoryRow.status == MemoryStatus.ACTIVE.value, or_(*conditions))
            .order_by(MemoryRow.created_at.desc())
            .limit(limit)
        )
        rows = (await self._session.execute(stmt)).scalars().all()
        return [self.to_view(r) for r in rows]

    async def touch(self, memory_ids: Sequence[str], *, when: datetime) -> None:
        """Bump recall salience/usage for the given memories (best-effort)."""
        if not memory_ids:
            return
        await self._session.execute(
            update(MemoryRow)
            .where(MemoryRow.id.in_(list(memory_ids)))
            .values(
                use_count=MemoryRow.use_count + 1,
                last_recalled_at=when,
                salience=func.min(MemoryRow.salience + 0.1, 5.0),
                updated_at=when,
            )
        )

    async def set_pinned(self, memory_id: str, pinned: bool) -> MemoryView | None:
        """Pin/unpin a memory (pinned rows are prune-exempt and recall-preferred)."""
        row = await self._session.get(MemoryRow, memory_id)
        if row is None:
            return None
        row.pinned = pinned
        row.updated_at = _utcnow()
        await self._session.flush()
        return self.to_view(row)

    async def delete(self, memory_id: str) -> bool:
        row = await self._session.get(MemoryRow, memory_id)
        if row is None:
            return False
        await self._session.delete(row)
        return True

    async def purge(self) -> int:
        result = await self._session.execute(delete(MemoryRow))
        return result.rowcount or 0

    async def count_active(self) -> int:
        stmt = (
            select(func.count())
            .select_from(MemoryRow)
            .where(MemoryRow.status == MemoryStatus.ACTIVE.value)
        )
        return int((await self._session.execute(stmt)).scalar_one())

    async def prune(
        self, *, max_records: int, expiry_days: int, now: datetime | None = None
    ) -> list[str]:
        """Enforce the store bound and expiry (RFC-0003 §6, invariant I-22).

        Soft-expires the lowest-retention active, non-pinned memories beyond
        ``max_records``, then hard-deletes rows soft-expired longer than
        ``expiry_days``. Returns the ids soft-expired in this call.
        """
        now = now or _utcnow()
        expired: list[str] = []
        total_active = await self.count_active()
        over = total_active - max_records
        if over > 0:
            candidates = (
                await self._session.execute(
                    select(MemoryRow).where(
                        MemoryRow.status == MemoryStatus.ACTIVE.value,
                        MemoryRow.pinned.is_(False),
                    )
                )
            ).scalars().all()
            candidates.sort(key=lambda r: _retention_key(r, now))
            for row in candidates[:over]:
                row.status = MemoryStatus.EXPIRED.value
                row.updated_at = now
                expired.append(row.id)

        cutoff = now - timedelta(days=expiry_days)
        await self._session.execute(
            delete(MemoryRow).where(
                MemoryRow.status == MemoryStatus.EXPIRED.value,
                MemoryRow.updated_at < cutoff,
            )
        )
        await self._session.flush()
        return expired

    @staticmethod
    def to_view(row: MemoryRow) -> MemoryView:
        return MemoryView(
            id=row.id,
            run_id=row.run_id,
            goal=row.goal,
            outcome=MemoryOutcome(row.outcome),
            success=row.success,
            summary=row.summary,
            lessons=row.lessons,
            tools_used=list(row.tools_used or []),
            task_count=row.task_count,
            source=MemorySource(row.source),
            salience=row.salience,
            use_count=row.use_count,
            last_recalled_at=row.last_recalled_at,
            pinned=row.pinned,
            status=MemoryStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
