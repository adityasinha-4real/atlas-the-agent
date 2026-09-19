"""Episodic memory persistence: MemoryRepository, FTS5, prune, migration (M5)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, text

from atlas.memory.schemas import (
    MemoryOutcome,
    MemoryRecord,
    MemorySource,
    MemoryStatus,
)
from atlas.persistence.database import Database
from atlas.persistence.models import MemoryRow
from atlas.persistence.repositories import MemoryRepository, RunRepository


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _record(
    *,
    run_id: str | None = None,
    goal: str = "goal",
    outcome: MemoryOutcome = MemoryOutcome.DONE,
    summary: str = "did the thing",
    lessons: str = "a useful lesson",
    tools: list[str] | None = None,
) -> MemoryRecord:
    return MemoryRecord(
        run_id=run_id,
        goal=goal,
        outcome=outcome,
        summary=summary,
        lessons=lessons,
        tools_used=tools or [],
        task_count=2,
        source=MemorySource.HEURISTIC,
    )


# --------------------------------------------------------------------------- #
# Migration / schema
# --------------------------------------------------------------------------- #


async def test_fts5_index_is_created(database: Database) -> None:
    """create_all builds the memories_fts virtual table (RFC-0003 §18)."""
    async with database.session() as session:
        rows = (
            await session.execute(
                text("SELECT name FROM sqlite_master WHERE name = 'memories_fts'")
            )
        ).all()
    assert rows, "memories_fts should exist when FTS5 is available"


async def test_light_migration_adds_memories_to_v040_db(tmp_path: Path) -> None:
    """A v0.4.0-shaped DB (no memories table) gains it + the FTS index."""
    db_file = tmp_path / "legacy_m4.db"
    conn = sqlite3.connect(db_file)
    conn.executescript(
        """
        CREATE TABLE runs (
            id TEXT PRIMARY KEY, goal TEXT NOT NULL, status TEXT NOT NULL,
            answer TEXT, error TEXT,
            created_at TIMESTAMP NOT NULL, updated_at TIMESTAMP NOT NULL
        );
        INSERT INTO runs (id, goal, status, created_at, updated_at)
            VALUES ('r0', 'legacy goal', 'done', '2026-01-01', '2026-01-01');
        """
    )
    conn.commit()
    conn.close()

    db = Database(_sqlite_url(db_file))
    try:
        await db.create_all()  # additive: creates memories + FTS index
        async with db.session() as session:
            view = await MemoryRepository(session).create(
                _record(run_id="r0", goal="legacy goal")
            )
            assert view.id.startswith("mem_")
        # Idempotent second run is a harmless no-op.
        await db.create_all()
        async with db.session() as session:
            found = await MemoryRepository(session).search("legacy goal", limit=5)
            assert [m.goal for m in found] == ["legacy goal"]
    finally:
        await db.dispose()


# --------------------------------------------------------------------------- #
# CRUD + upsert
# --------------------------------------------------------------------------- #


async def test_create_and_get_roundtrip(database: Database) -> None:
    async with database.session() as session:
        view = await MemoryRepository(session).create(
            _record(goal="convert km to miles", outcome=MemoryOutcome.DONE)
        )
    async with database.session() as session:
        got = await MemoryRepository(session).get(view.id)
    assert got is not None
    assert got.goal == "convert km to miles"
    assert got.outcome is MemoryOutcome.DONE
    assert got.success is True  # derived from outcome
    assert got.status is MemoryStatus.ACTIVE
    assert got.salience == 1.0


async def test_create_upserts_on_run_id(database: Database) -> None:
    """One memory per run: a second write for the same run_id updates in place."""
    async with database.session() as session:
        await RunRepository(session).create("run-1", "a goal")
    async with database.session() as session:
        repo = MemoryRepository(session)
        first = await repo.create(_record(run_id="run-1", lessons="v1"))
    async with database.session() as session:
        repo = MemoryRepository(session)
        second = await repo.create(_record(run_id="run-1", lessons="v2"))
    assert first.id == second.id
    async with database.session() as session:
        rows = await MemoryRepository(session).list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0].lessons == "v2"


async def test_null_run_ids_do_not_collide(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        await repo.create(_record(run_id=None, goal="a"))
        await repo.create(_record(run_id=None, goal="b"))
    async with database.session() as session:
        rows = await MemoryRepository(session).list_recent(limit=10)
    assert len(rows) == 2


async def test_memory_survives_run_deletion(database: Database) -> None:
    """run_id is ON DELETE SET NULL — a lesson outlives its run (RFC-0003 §5)."""
    async with database.session() as session:
        await RunRepository(session).create("run-x", "some goal")
    async with database.session() as session:
        await MemoryRepository(session).create(_record(run_id="run-x"))
    async with database.session() as session:
        run = await RunRepository(session).get("run-x")
        await session.delete(run)
    async with database.session() as session:
        rows = await MemoryRepository(session).list_recent(limit=10)
    assert len(rows) == 1
    assert rows[0].run_id is None


# --------------------------------------------------------------------------- #
# Search
# --------------------------------------------------------------------------- #


async def test_fts_search_finds_relevant_only(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        await repo.create(_record(goal="find the tallest mountain in meters"))
        await repo.create(_record(goal="bake a chocolate cake recipe"))
    async with database.session() as session:
        hits = await MemoryRepository(session).search("tallest mountain height", limit=5)
    goals = [m.goal for m in hits]
    assert "find the tallest mountain in meters" in goals
    assert "bake a chocolate cake recipe" not in goals


async def test_search_unrelated_goal_returns_nothing(database: Database) -> None:
    async with database.session() as session:
        await MemoryRepository(session).create(_record(goal="convert units of length"))
    async with database.session() as session:
        hits = await MemoryRepository(session).search("photosynthesis biology", limit=5)
    assert hits == []


async def test_search_excludes_expired(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        view = await repo.create(_record(goal="unique searchterm alpha"))
    async with database.session() as session:
        # Soft-expire by pruning to zero capacity.
        await MemoryRepository(session).prune(max_records=0, expiry_days=365)
    async with database.session() as session:
        hits = await MemoryRepository(session).search("searchterm alpha", limit=5)
    assert view.id not in [m.id for m in hits]


async def test_like_search_fallback_matches(database: Database) -> None:
    """The LIKE fallback (used when FTS5 is absent) retrieves by substring."""
    async with database.session() as session:
        await MemoryRepository(session).create(
            _record(goal="compute compound interest", lessons="use calculator")
        )
    async with database.session() as session:
        repo = MemoryRepository(session)
        hits = await repo._like_search(["compound", "interest"], limit=5)
    assert [m.goal for m in hits] == ["compute compound interest"]


# --------------------------------------------------------------------------- #
# Salience / prune / purge
# --------------------------------------------------------------------------- #


async def test_touch_bumps_usage_and_salience(database: Database) -> None:
    async with database.session() as session:
        view = await MemoryRepository(session).create(_record())
    when = datetime.now(UTC)
    async with database.session() as session:
        await MemoryRepository(session).touch([view.id], when=when)
    async with database.session() as session:
        got = await MemoryRepository(session).get(view.id)
    assert got.use_count == 1
    assert got.salience > 1.0
    assert got.last_recalled_at is not None


async def test_prune_evicts_lowest_and_keeps_pinned(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        keep = await repo.create(_record(goal="keep me"))
        await repo.create(_record(goal="drop me"))
        # Pin the one we want to survive regardless of retention score.
        row = await session.get(MemoryRow, keep.id)
        row.pinned = True
    async with database.session() as session:
        expired = await MemoryRepository(session).prune(max_records=1, expiry_days=365)
    assert len(expired) == 1
    async with database.session() as session:
        active = await MemoryRepository(session).list_recent(limit=10)
    assert [m.goal for m in active] == ["keep me"]


async def test_prune_hard_deletes_stale_expired(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        await repo.create(_record(goal="one two three"))
        await repo.create(_record(goal="four five six"))
    # First prune soft-expires the overflow; backdate it, then prune again to
    # trigger the hard delete.
    async with database.session() as session:
        await MemoryRepository(session).prune(max_records=1, expiry_days=30)
    async with database.session() as session:
        stale = datetime(2000, 1, 1)  # naive & clearly older than any expiry window
        rows = (
            await session.execute(
                select(MemoryRow).where(MemoryRow.status == "expired")
            )
        ).scalars().all()
        for row in rows:
            row.updated_at = stale
    async with database.session() as session:
        await MemoryRepository(session).prune(max_records=1, expiry_days=30)
    async with database.session() as session:
        remaining = (
            await session.execute(text("SELECT COUNT(*) FROM memories"))
        ).scalar_one()
    assert remaining == 1


async def test_purge_removes_all(database: Database) -> None:
    async with database.session() as session:
        repo = MemoryRepository(session)
        await repo.create(_record(goal="alpha"))
        await repo.create(_record(goal="beta"))
    async with database.session() as session:
        deleted = await MemoryRepository(session).purge()
    assert deleted == 2
    async with database.session() as session:
        assert await MemoryRepository(session).list_recent(limit=10) == []


async def test_memory_view_serializes(database: Database) -> None:
    async with database.session() as session:
        view = await MemoryRepository(session).create(_record(goal="serialize me"))
    dumped = view.model_dump()
    assert dumped["goal"] == "serialize me"
    assert dumped["outcome"] == "done"
    assert dumped["status"] == "active"
    assert dumped["tools_used"] == []
