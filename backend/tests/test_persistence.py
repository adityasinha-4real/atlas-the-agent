"""Repository behavior: run CRUD, the event ledger, and the task list."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import delete
from sqlalchemy.exc import IntegrityError

from atlas.agent.schemas import (
    PlannedTask,
    ReflectionDecision,
    ReflectionResult,
    ReflectionSource,
    RunStatus,
    TaskStatus,
)
from atlas.events.types import EventType
from atlas.persistence.database import Database
from atlas.persistence.models import RunRow, TaskAttemptRow, TaskRow
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskAttemptRepository,
    TaskRepository,
)


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


async def test_run_create_and_projection_updates(database: Database) -> None:
    async with database.session() as session:
        repo = RunRepository(session)
        await repo.create("run1", "do a thing")

    async with database.session() as session:
        repo = RunRepository(session)
        await repo.set_status("run1", RunStatus.RUNNING)
        await repo.set_answer("run1", "the answer")
        await repo.set_status("run1", RunStatus.DONE)

    async with database.session() as session:
        row = await RunRepository(session).get("run1")
        assert row is not None
        view = RunRepository.to_view(row)
        assert view.status is RunStatus.DONE
        assert view.answer == "the answer"


async def test_event_seq_is_monotonic(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("run2", "goal")

    seqs: list[int] = []
    for _ in range(3):
        async with database.session() as session:
            repo = EventRepository(session)
            seq = await repo.next_seq("run2")
            await repo.append("run2", seq, EventType.LOG, {"n": seq})
            seqs.append(seq)

    assert seqs == [1, 2, 3]


async def test_list_after_filters_by_seq(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("run3", "goal")
    async with database.session() as session:
        repo = EventRepository(session)
        for i in range(1, 4):
            await repo.append("run3", i, EventType.LOG, {"i": i})

    async with database.session() as session:
        events = await EventRepository(session).list_after("run3", after_seq=1)
        assert [e.seq for e in events] == [2, 3]


async def test_tasks_bulk_create_and_ordered_read(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runT", "multi-step goal")
        views = await TaskRepository(session).bulk_create(
            "runT",
            [
                PlannedTask(description="first", success_criteria="did first"),
                PlannedTask(description="second", suggested_tool="calculator"),
            ],
        )

    assert [v.index for v in views] == [0, 1]
    assert [v.id for v in views] == ["runT:0", "runT:1"]
    assert all(v.status is TaskStatus.PENDING for v in views)

    async with database.session() as session:
        tasks = await TaskRepository(session).list_for_run("runT")
        assert [t.description for t in tasks] == ["first", "second"]
        assert tasks[1].suggested_tool == "calculator"


async def test_task_status_transitions(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runS", "goal")
        await TaskRepository(session).bulk_create(
            "runS", [PlannedTask(description="only")]
        )

    async with database.session() as session:
        repo = TaskRepository(session)
        await repo.mark_running("runS:0")

    async with database.session() as session:
        repo = TaskRepository(session)
        await repo.mark_done("runS:0", "the output")

    async with database.session() as session:
        tasks = await TaskRepository(session).list_for_run("runS")
        assert tasks[0].status is TaskStatus.DONE
        assert tasks[0].output == "the output"


async def test_tasks_cascade_delete_with_run(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runC", "goal")
        await TaskRepository(session).bulk_create(
            "runC", [PlannedTask(description="a"), PlannedTask(description="b")]
        )

    # DB-level ON DELETE CASCADE (foreign_keys=ON) removes the run's tasks.
    async with database.session() as session:
        await session.execute(delete(RunRow).where(RunRow.id == "runC"))

    async with database.session() as session:
        remaining = await session.get(TaskRow, "runC:0")
        assert remaining is None


# --------------------------------------------------------------------------- #
# M4: task attempts, transitions, and light migration (RFC-0002)
# --------------------------------------------------------------------------- #


async def test_task_attempts_create_and_ordered_read(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runA", "goal")
        await TaskRepository(session).bulk_create(
            "runA", [PlannedTask(description="t")]
        )
        repo = TaskAttemptRepository(session)
        await repo.create(
            task_id="runA:0", run_id="runA", attempt_number=1, output="first"
        )
        await repo.create(
            task_id="runA:0", run_id="runA", attempt_number=2, output="second"
        )

    async with database.session() as session:
        attempts = await TaskAttemptRepository(session).list_for_task("runA:0")

    assert [a.attempt_number for a in attempts] == [1, 2]
    assert [a.attempt_id for a in attempts] == ["runA:0#1", "runA:0#2"]
    assert attempts[0].output == "first"
    # Reflection fields are null until the attempt is judged.
    assert attempts[0].reflection_decision is None


async def test_task_attempt_record_reflection(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runR", "goal")
        await TaskRepository(session).bulk_create(
            "runR", [PlannedTask(description="t")]
        )
        await TaskAttemptRepository(session).create(
            task_id="runR:0", run_id="runR", attempt_number=1, output="o"
        )

    async with database.session() as session:
        await TaskAttemptRepository(session).record_reflection(
            "runR:0#1",
            ReflectionResult(
                decision=ReflectionDecision.RETRY,
                reason="needs work",
                confidence=0.7,
                source=ReflectionSource.LLM,
                reflection_version=1,
            ),
        )

    async with database.session() as session:
        attempt = (await TaskAttemptRepository(session).list_for_task("runR:0"))[0]

    assert attempt.reflection_decision is ReflectionDecision.RETRY
    assert attempt.reflection_reason == "needs work"
    assert attempt.reflection_confidence == 0.7
    assert attempt.reflection_source is ReflectionSource.LLM
    assert attempt.reflection_version == 1


async def test_task_attempt_number_unique_per_task(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runU", "goal")
        await TaskRepository(session).bulk_create(
            "runU", [PlannedTask(description="t")]
        )

    with pytest.raises(IntegrityError):
        async with database.session() as session:
            repo = TaskAttemptRepository(session)
            await repo.create(task_id="runU:0", run_id="runU", attempt_number=1)
            await repo.create(task_id="runU:0", run_id="runU", attempt_number=1)


async def test_task_attempts_cascade_delete_with_run(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runX", "goal")
        await TaskRepository(session).bulk_create(
            "runX", [PlannedTask(description="t")]
        )
        await TaskAttemptRepository(session).create(
            task_id="runX:0", run_id="runX", attempt_number=1, output="o"
        )

    async with database.session() as session:
        await session.execute(delete(RunRow).where(RunRow.id == "runX"))

    async with database.session() as session:
        remaining = await session.get(TaskAttemptRow, "runX:0#1")
        assert remaining is None


async def test_task_m4_transitions_and_generation(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runM", "goal")
        await TaskRepository(session).bulk_create(
            "runM",
            [
                PlannedTask(description="a"),
                PlannedTask(description="b"),
                PlannedTask(description="c"),
            ],
        )

    async with database.session() as session:
        repo = TaskRepository(session)
        await repo.mark_retrying("runM:0")
        await repo.set_generation("runM:0", generation=1, parent_generation=0)
        await repo.mark_cancelled("runM:1")
        await repo.bulk_skip(["runM:2"])

    async with database.session() as session:
        tasks = await TaskRepository(session).list_for_run("runM")
        row = await session.get(TaskRow, "runM:0")

    assert tasks[0].status is TaskStatus.RETRYING
    assert tasks[1].status is TaskStatus.CANCELLED
    assert tasks[2].status is TaskStatus.SKIPPED
    assert row is not None
    assert row.replan_generation == 1
    assert row.parent_generation == 0


async def test_new_task_defaults_for_m4_columns(database: Database) -> None:
    async with database.session() as session:
        await RunRepository(session).create("runD", "goal")
        await TaskRepository(session).bulk_create(
            "runD", [PlannedTask(description="t")]
        )

    async with database.session() as session:
        row = await session.get(TaskRow, "runD:0")

    assert row is not None
    assert row.attempt_count == 0
    assert row.replan_generation == 0
    assert row.parent_generation is None


async def test_light_migration_upgrades_v030_tasks(tmp_path: Path) -> None:
    """A v0.3.0-shaped ``tasks`` table gains the M4 columns, idempotently."""
    db_file = tmp_path / "legacy.db"
    # Simulate a database created before M4: ``tasks`` lacks the new columns and
    # there is no ``task_attempts`` table at all.
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
            answer TEXT, error TEXT,
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        CREATE TABLE tasks (
            id TEXT PRIMARY KEY, run_id TEXT NOT NULL, "index" INTEGER NOT NULL,
            description TEXT NOT NULL, success_criteria TEXT NOT NULL DEFAULT '',
            suggested_tool TEXT, status TEXT NOT NULL, output TEXT, error TEXT,
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        INSERT INTO runs (id, goal, status, created_at, updated_at)
            VALUES ('r0', 'legacy goal', 'done', '2026-01-01', '2026-01-01');
        INSERT INTO tasks (id, run_id, "index", description, success_criteria,
            status, output, created_at, updated_at)
            VALUES ('r0:0', 'r0', 0, 'legacy task', 'crit', 'done', 'out',
            '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    db = Database(_sqlite_url(db_file))
    try:
        await db.create_all()  # create_all + light migration
        async with db.session() as session:
            row = await session.get(TaskRow, "r0:0")
            assert row is not None
            # Existing data preserved, new columns back-filled with defaults.
            assert row.output == "out"
            assert row.attempt_count == 0
            assert row.replan_generation == 0
            assert row.parent_generation is None
            # The new table was created too.
            await TaskAttemptRepository(session).create(
                task_id="r0:0", run_id="r0", attempt_number=1, output="new"
            )
        # Running the migration again is a harmless no-op (idempotent).
        await db.create_all()
        async with db.session() as session:
            attempts = await TaskAttemptRepository(session).list_for_task("r0:0")
            assert [a.attempt_number for a in attempts] == [1]
    finally:
        await db.dispose()
