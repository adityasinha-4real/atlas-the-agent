"""Startup crash reconciler (RFC-0004 §13, ADR-0021).

After an abrupt shutdown, runs may be left in a non-terminal state (``created`` /
``planning`` / ``running``) with no live orchestrating task. On the next startup the
reconciler brings each to a **consistent terminal state derived solely from the
ledger** (invariant I-26). It does **not** resume execution — resumability is a
larger, later design; M6 guarantees consistency and no orphaned non-terminal rows.

Behavior:
* If the ledger already contains a terminal run event, the run genuinely finished
  just before the crash; the row is reconciled to match — no new event is invented.
* Otherwise the run was interrupted mid-flight: its non-terminal tasks are marked
  ``cancelled`` (with attributed ``task.cancelled`` events), the run is finalized
  ``FAILED`` with reason ``interrupted_by_shutdown``, and a single, clearly-attributed
  ``run.failed`` event (``recovered: true``) is appended.

Idempotent: a second pass finds only terminal runs and does nothing.
"""

from __future__ import annotations

import logging

from atlas.agent.schemas import RunStatus, TaskStatus
from atlas.events.emit import EventEmitter
from atlas.events.types import EventType
from atlas.persistence.database import Database
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskRepository,
)
from atlas.recovery.replay import fold_events

logger = logging.getLogger(__name__)

_INTERRUPTED = "interrupted_by_shutdown"
_NON_TERMINAL_TASKS = {
    TaskStatus.PENDING,
    TaskStatus.RUNNING,
    TaskStatus.RETRYING,
}


class Reconciler:
    """Reconciles interrupted runs to a terminal state at startup."""

    def __init__(self, db: Database, emitter: EventEmitter) -> None:
        self._db = db
        self._emitter = emitter

    async def reconcile_pending(self) -> list[str]:
        """Reconcile every non-terminal run. Returns the reconciled run ids."""
        async with self._db.session() as session:
            rows = await RunRepository(session).list_non_terminal()
            pending = [r.id for r in rows]
        if not pending:
            return []

        reconciled: list[str] = []
        for run_id in pending:
            try:
                if await self._reconcile_one(run_id):
                    reconciled.append(run_id)
            except Exception:  # noqa: BLE001 - one bad run must not block startup
                logger.exception("reconcile failed for run %s", run_id)
        if reconciled:
            logger.warning(
                "recovered %d interrupted run(s) at startup: %s",
                len(reconciled),
                ", ".join(reconciled),
            )
        return reconciled

    async def _reconcile_one(self, run_id: str) -> bool:
        async with self._db.session() as session:
            events = await EventRepository(session).list_after(run_id, 0)
        state = fold_events(events)

        if state.terminal:
            # The ledger already reached a terminal event; the row status simply
            # never caught up. Reconcile the row to the ledger — invent nothing.
            async with self._db.session() as session:
                await RunRepository(session).set_status(run_id, state.status)
            logger.info("reconciled stale row for run %s -> %s", run_id, state.status)
            return True

        # Genuinely interrupted: terminalize leftover tasks, then fail the run.
        await self._cancel_open_tasks(run_id)
        error = "Run interrupted by shutdown before completion"
        async with self._db.session() as session:
            repo = RunRepository(session)
            await repo.set_error(run_id, error)
            await repo.set_status(run_id, RunStatus.FAILED)
        await self._emitter.emit(
            run_id,
            EventType.RUN_FAILED,
            {"error": error, "reason": _INTERRUPTED, "recovered": True},
        )
        return True

    async def _cancel_open_tasks(self, run_id: str) -> None:
        """Mark any non-terminal task cancelled + emit an attributed event (I-4)."""
        async with self._db.session() as session:
            repo = TaskRepository(session)
            tasks = await repo.list_for_run(run_id)
            open_tasks = [t for t in tasks if t.status in _NON_TERMINAL_TASKS]
            for task in open_tasks:
                await repo.mark_cancelled(task.id)
        for task in open_tasks:
            await self._emitter.emit(
                run_id,
                EventType.TASK_CANCELLED,
                {"task_id": task.id, "index": task.index, "reason": _INTERRUPTED},
            )
