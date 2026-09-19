"""Crash reconciler: interrupted runs → consistent terminal state (M6 §13, I-26)."""

from __future__ import annotations

from atlas.agent.schemas import PlannedTask, RunStatus, TaskStatus
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.persistence.database import Database
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskRepository,
)
from atlas.recovery.reconciler import Reconciler


def _emitter(db: Database) -> EventEmitter:
    return EventEmitter(db, EventHub())


async def _seed_interrupted(db: Database, run_id: str) -> None:
    """A run left mid-flight: RUNNING, a plan, one task done and one running."""
    async with db.session() as session:
        run_repo = RunRepository(session)
        await run_repo.create(run_id, "interrupted goal")
        await run_repo.set_status(run_id, RunStatus.RUNNING)
        tasks = await TaskRepository(session).bulk_create(
            run_id,
            [PlannedTask(description="first"), PlannedTask(description="second")],
        )
        await TaskRepository(session).mark_done(tasks[0].id, "first output")
        await TaskRepository(session).mark_running(tasks[1].id)
    emitter = _emitter(db)
    await emitter.emit(run_id, EventType.RUN_CREATED, {"goal": "interrupted goal"})
    await emitter.emit(run_id, EventType.RUN_STARTED, {})
    await emitter.emit(
        run_id, EventType.TASK_STARTED, {"task_id": f"{run_id}:1", "index": 1}
    )


async def _events(db: Database, run_id: str) -> list:
    async with db.session() as session:
        return await EventRepository(session).list_after(run_id, 0)


async def test_interrupted_run_is_finalized_failed(database: Database) -> None:
    await _seed_interrupted(database, "run-int")
    reconciled = await Reconciler(database, _emitter(database)).reconcile_pending()
    assert reconciled == ["run-int"]

    async with database.session() as session:
        run = await RunRepository(session).get("run-int")
    assert run.status == RunStatus.FAILED.value
    assert "interrupted" in run.error.lower()

    events = await _events(database, "run-int")
    failed = [e for e in events if e.type is EventType.RUN_FAILED]
    assert len(failed) == 1
    assert failed[0].payload["recovered"] is True
    assert failed[0].payload["reason"] == "interrupted_by_shutdown"


async def test_reconcile_leaves_no_nonterminal_tasks(database: Database) -> None:
    await _seed_interrupted(database, "run-tasks")
    await Reconciler(database, _emitter(database)).reconcile_pending()
    async with database.session() as session:
        tasks = await TaskRepository(session).list_for_run("run-tasks")
    statuses = {t.status for t in tasks}
    assert TaskStatus.PENDING not in statuses
    assert TaskStatus.RUNNING not in statuses
    # The completed task's output is preserved; the open one is cancelled.
    by_index = {t.index: t for t in tasks}
    assert by_index[0].status is TaskStatus.DONE
    assert by_index[0].output == "first output"
    assert by_index[1].status is TaskStatus.CANCELLED


async def test_reconcile_is_idempotent(database: Database) -> None:
    await _seed_interrupted(database, "run-idem")
    rec = Reconciler(database, _emitter(database))
    first = await rec.reconcile_pending()
    count_after_first = len(await _events(database, "run-idem"))
    second = await rec.reconcile_pending()
    count_after_second = len(await _events(database, "run-idem"))
    assert first == ["run-idem"]
    assert second == []  # nothing left non-terminal
    assert count_after_first == count_after_second  # no duplicate events


async def test_no_pending_runs_is_noop(database: Database) -> None:
    assert await Reconciler(database, _emitter(database)).reconcile_pending() == []


async def test_terminal_ledger_reconciles_row_without_new_event(
    database: Database,
) -> None:
    """A row left RUNNING but whose ledger already completed is just corrected."""
    run_id = "run-stale"
    async with database.session() as session:
        await RunRepository(session).create(run_id, "finished but stale row")
        await RunRepository(session).set_status(run_id, RunStatus.RUNNING)
    emitter = _emitter(database)
    await emitter.emit(run_id, EventType.RUN_CREATED, {})
    await emitter.emit(run_id, EventType.ANSWER_COMPLETED, {"text": "answer"})
    await emitter.emit(run_id, EventType.RUN_COMPLETED, {})
    before = len(await _events(database, run_id))

    reconciled = await Reconciler(database, emitter).reconcile_pending()
    assert reconciled == [run_id]
    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
    assert run.status == RunStatus.DONE.value  # reconciled to the ledger
    # No fabricated event — the ledger already told the whole story (I-26).
    assert len(await _events(database, run_id)) == before
