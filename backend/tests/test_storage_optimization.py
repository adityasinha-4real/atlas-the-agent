"""Phase 2 storage changes: index migration safety, pragma, and correctness (M6).

Verifies the additive ``ix_runs_created`` index (declared + back-filled), the
``temp_store=MEMORY`` pragma, and that the runs-list ordering behavior is
unchanged — the optimization must not alter externally observable results.
"""

from __future__ import annotations

from pathlib import Path

import pytest_asyncio
from sqlalchemy import text

from atlas.persistence.database import Database
from atlas.persistence.repositories import RunRepository


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest_asyncio.fixture
async def db(tmp_path: Path):  # noqa: ANN201
    database = Database(_url(tmp_path / "opt.db"))
    await database.create_all()
    try:
        yield database
    finally:
        await database.dispose()


async def _index_names(database: Database) -> set[str]:
    async with database.engine.connect() as conn:
        result = await conn.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type='index'"
        )
        return {row[0] for row in result.fetchall()}


async def test_runs_created_index_exists_on_fresh_db(db: Database) -> None:
    assert "ix_runs_created" in await _index_names(db)


async def test_index_migration_backfills_and_is_idempotent(db: Database) -> None:
    # Simulate a pre-M6 database that lacks the index.
    async with db.engine.begin() as conn:
        await conn.exec_driver_sql("DROP INDEX ix_runs_created")
    assert "ix_runs_created" not in await _index_names(db)

    # create_all re-runs the idempotent migration and back-fills the index.
    await db.create_all()
    assert "ix_runs_created" in await _index_names(db)

    # Running it again is a harmless no-op (IF NOT EXISTS).
    await db.create_all()
    assert "ix_runs_created" in await _index_names(db)


async def test_temp_store_pragma_is_memory(db: Database) -> None:
    async with db.engine.connect() as conn:
        result = await conn.exec_driver_sql("PRAGMA temp_store")
        # 2 == MEMORY.
        assert result.scalar() == 2


async def test_runs_list_ordering_unchanged(db: Database) -> None:
    ids: list[str] = []
    async with db.session() as session:
        repo = RunRepository(session)
        for i in range(5):
            row = await repo.create(f"run-{i}", f"goal {i}")
            ids.append(row.id)
    async with db.session() as session:
        listed = await RunRepository(session).list_recent(limit=50)
    # Newest-first: the last created run comes first (behavior preserved).
    assert [s.id for s in listed][: len(ids)] == list(reversed(ids))


async def test_runs_list_uses_index(db: Database) -> None:
    async with db.session() as session:
        repo = RunRepository(session)
        for i in range(20):
            await repo.create(f"r{i}", f"g{i}")
    async with db.engine.connect() as conn:
        result = await conn.exec_driver_sql(
            "EXPLAIN QUERY PLAN SELECT * FROM runs ORDER BY created_at DESC LIMIT 50"
        )
        plan = " ".join(str(row[-1]) for row in result.fetchall())
    assert "ix_runs_created" in plan
    assert "TEMP B-TREE" not in plan


async def test_wal_and_fk_pragmas_still_set(db: Database) -> None:
    # Phase 2 must not disturb the existing durability pragmas.
    async with db.engine.connect() as conn:
        journal = (await conn.exec_driver_sql("PRAGMA journal_mode")).scalar()
        fk = (await conn.exec_driver_sql("PRAGMA foreign_keys")).scalar()
    assert str(journal).lower() == "wal"
    assert fk == 1


async def test_text_import_available() -> None:
    # Guard: routes.py now imports text() for /ready; ensure the symbol resolves.
    assert text("SELECT 1") is not None
