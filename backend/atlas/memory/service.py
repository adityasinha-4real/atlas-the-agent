"""``MemoryService`` — the best-effort façade the runtime holds (RFC-0003 §2).

Combines recall (plan time) and write (finalization) over a ``MemoryStore``, emits
the ``memory.*`` events, and guarantees that **no** memory operation can affect a
run: every public method swallows and logs its own failures (invariant I-14). The
runtime constructs this only when ``memory_enabled`` is true, so with memory off
there is no memory object and behavior is byte-for-byte M4 (invariant I-15).
"""

from __future__ import annotations

import logging

from atlas.agent.schemas import TaskStatus
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.types import EventType
from atlas.llm.gateway import LLMGateway
from atlas.memory.recall import RecallService
from atlas.memory.schemas import MemoryOutcome, RecallResult
from atlas.memory.store import EpisodicStore, MemoryStore
from atlas.memory.writer import MemoryWriter
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository, TaskRepository
from atlas.runtime.budget import BudgetCategory, BudgetedGateway, RunBudget

logger = logging.getLogger(__name__)


class MemoryService:
    """Recall lessons at plan time and write a distilled memory at finalization."""

    def __init__(
        self,
        db: Database,
        gateway: LLMGateway,
        emitter: EventEmitter,
        settings: Settings,
    ) -> None:
        self._db = db
        self._gateway = gateway
        self._emitter = emitter
        self._settings = settings
        self._store: MemoryStore = EpisodicStore(db)
        self._recall = RecallService(self._store, settings)
        self._writer = MemoryWriter(settings)
        self._min_tasks = settings.memory_min_tasks
        self._write_outcomes = set(settings.memory_write_outcomes)
        self._distill_with_llm = settings.memory_distill_with_llm
        self._max_records = settings.memory_max_records
        self._expiry_days = settings.memory_expiry_days

    @property
    def store(self) -> MemoryStore:
        """The underlying store (for the read-only memory API, later phase)."""
        return self._store

    async def recall(
        self, run_id: str, goal: str, budget: RunBudget
    ) -> RecallResult:
        """Recall lessons and emit ``memory.recalled``. Best-effort → empty on error.

        ``budget`` is unused today (recall makes no model call) but is accepted so
        the signature is stable if recall later spends model calls.
        """
        try:
            result = await self._recall.recall(goal)
            if result.is_empty:
                return result
            await self._store.touch(result.memory_ids)
            await self._emitter.emit(
                run_id,
                EventType.MEMORY_RECALLED,
                {
                    "query": goal,
                    "count": len(result.memories),
                    "memory_ids": result.memory_ids,
                    "rendered": result.rendered,
                },
            )
            return result
        except Exception:  # noqa: BLE001 - memory never fails a run (I-14)
            logger.warning("Memory recall failed for run %s", run_id, exc_info=True)
            return RecallResult.empty()

    async def write(
        self,
        run_id: str,
        goal: str,
        outcome: MemoryOutcome,
        answer: str,
        budget: RunBudget,
    ) -> None:
        """Distill and persist a memory, then prune. Best-effort — never raises.

        Called only after the answer has been delivered and the run finalized, and
        only for the configured outcomes (``done``/``partial``); it is never invoked
        on budget-exhaustion or cancellation (no call site there), satisfying
        RFC-0003 §13.
        """
        try:
            if outcome.value not in self._write_outcomes:
                return
            tasks, tools = await self._gather(run_id)
            if len(tasks) < self._min_tasks:
                return
            gateway = (
                BudgetedGateway(self._gateway, budget, BudgetCategory.MEMORY)
                if self._distill_with_llm
                else None
            )
            record = await self._writer.distill(
                goal=goal,
                outcome=outcome,
                answer=answer,
                task_descriptions=[t.description for t in tasks],
                tools_used=tools,
                task_count=len(tasks),
                run_id=run_id,
                gateway=gateway,
            )
            view = await self._store.write(record)
            pruned = await self._store.prune(
                max_records=self._max_records, expiry_days=self._expiry_days
            )
            await self._emitter.emit(
                run_id,
                EventType.MEMORY_WRITTEN,
                {
                    "memory_id": view.id,
                    "run_id": run_id,
                    "outcome": outcome.value,
                    "source": record.source.value,
                    "pruned": pruned,
                },
            )
        except Exception:  # noqa: BLE001 - memory never fails a run (I-14)
            logger.warning("Memory write failed for run %s", run_id, exc_info=True)

    async def _gather(self, run_id: str) -> tuple[list, list[str]]:
        """Collect the run's tasks and the distinct tools it actually called."""
        async with self._db.session() as session:
            tasks = await TaskRepository(session).list_for_run(run_id)
            events = await EventRepository(session).list_after(run_id, 0)
        # Count the tasks that ran (not ones skipped by a replan/abort).
        ran = [t for t in tasks if t.status is not TaskStatus.SKIPPED]
        tools: list[str] = []
        for event in events:
            if event.type is EventType.TOOL_CALL:
                name = event.payload.get("tool")
                if name and name not in tools:
                    tools.append(name)
        return ran, tools
