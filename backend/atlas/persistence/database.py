"""Async engine/session factory and schema bootstrap.

SQLite is configured with WAL journaling and ``NORMAL`` synchronous mode for
low-latency writes without sacrificing durability across app crashes. A single
async engine is shared process-wide; sessions are short-lived.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from atlas.persistence.models import Base

logger = logging.getLogger(__name__)


def _apply_sqlite_pragmas(dbapi_connection, _record) -> None:  # noqa: ANN001
    """Enable WAL + sane pragmas on every new SQLite connection."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=5000")
        # Keep FTS/ORDER-BY temp b-trees in memory rather than spilling to disk
        # (M6/RFC-0004 §7). Affects only transient query scratch space, never the
        # database file — behavior-neutral, durability-neutral.
        cursor.execute("PRAGMA temp_store=MEMORY")
    finally:
        cursor.close()


# Columns added to pre-existing tables after their first release. ``create_all``
# only ever CREATEs missing tables, never ALTERs an existing one, so a database
# created by an earlier milestone needs these columns added explicitly. Each DDL
# fragment is nullable or defaulted so the back-fill on existing rows is trivial.
_LIGHT_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    # M4 (RFC-0002 §11.2 / ADR-0015): self-correction bookkeeping on ``tasks``.
    "tasks": [
        ("attempt_count", "INTEGER NOT NULL DEFAULT 0"),
        ("replan_generation", "INTEGER NOT NULL DEFAULT 0"),
        ("parent_generation", "INTEGER"),
    ],
}


def _apply_light_migrations(conn) -> None:  # noqa: ANN001 - sync Connection
    """Idempotently add any missing columns from ``_LIGHT_MIGRATIONS``.

    Guarded by ``PRAGMA table_info`` so it is a no-op on a freshly-created schema
    (where ``create_all`` already made every column) and safe to run on every
    startup. SQLite-only; a real migration tool (Alembic) is deferred until the
    Postgres move (design doc §7). Table/column names are internal constants, not
    user input, so the interpolated DDL is safe.
    """
    if conn.dialect.name != "sqlite":
        return
    for table, columns in _LIGHT_MIGRATIONS.items():
        rows = conn.exec_driver_sql(f"PRAGMA table_info({table})").fetchall()
        existing = {row[1] for row in rows}
        for name, ddl in columns:
            if name not in existing:
                conn.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


# Indexes added after a table's first release (M6/RFC-0004 §7, invariant I-27).
# ``create_all`` builds model-declared indexes on a fresh DB; these ``IF NOT
# EXISTS`` statements add them to databases created by an earlier milestone. Both
# paths are idempotent and additive — never a drop or rewrite.
_INDEX_MIGRATIONS: tuple[str, ...] = (
    "CREATE INDEX IF NOT EXISTS ix_runs_created ON runs (created_at)",
)


def _apply_index_migrations(conn) -> None:  # noqa: ANN001 - sync Connection
    """Idempotently create any performance indexes missing on an older DB."""
    if conn.dialect.name != "sqlite":
        return
    for ddl in _INDEX_MIGRATIONS:
        conn.exec_driver_sql(ddl)


# On-disk schema version (M6/RFC-0004 §14). Bump when a migration changes shape.
SCHEMA_VERSION = 1


def _stamp_schema_meta(conn) -> None:  # noqa: ANN001 - sync Connection
    """Record the schema + app version in ``schema_meta`` (idempotent upsert)."""
    from atlas import __version__

    for key, value in (
        ("schema_version", str(SCHEMA_VERSION)),
        ("app_version", __version__),
    ):
        conn.exec_driver_sql(
            "INSERT INTO schema_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )


# The full-text index over ``memories`` and its sync triggers (RFC-0003 §4/§18,
# ADR-0017). An FTS5 external-content table mirrors the searchable columns; three
# triggers keep it in sync inside the same transaction as each ``memories`` write.
# All DDL is ``IF NOT EXISTS`` so it is idempotent on every startup. The identifiers
# are internal constants, not user input.
_FTS5_TABLE = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5("
    "goal, summary, lessons, content='memories', content_rowid='rowid')"
)
_FTS5_TRIGGERS = (
    "CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN "
    "INSERT INTO memories_fts(rowid, goal, summary, lessons) "
    "VALUES (new.rowid, new.goal, new.summary, new.lessons); END",
    "CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN "
    "INSERT INTO memories_fts(memories_fts, rowid, goal, summary, lessons) "
    "VALUES ('delete', old.rowid, old.goal, old.summary, old.lessons); END",
    "CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN "
    "INSERT INTO memories_fts(memories_fts, rowid, goal, summary, lessons) "
    "VALUES ('delete', old.rowid, old.goal, old.summary, old.lessons); "
    "INSERT INTO memories_fts(rowid, goal, summary, lessons) "
    "VALUES (new.rowid, new.goal, new.summary, new.lessons); END",
)


def _apply_fts5(conn) -> None:  # noqa: ANN001 - sync Connection
    """Create the ``memories_fts`` index + triggers when FTS5 is available.

    If the SQLite build lacks FTS5, the ``CREATE VIRTUAL TABLE`` fails; that is
    caught and logged, and memory recall degrades to a ``LIKE`` scan (ADR-0017).
    Idempotent: ``IF NOT EXISTS`` makes re-runs a no-op.
    """
    if conn.dialect.name != "sqlite":
        return
    try:
        conn.exec_driver_sql(_FTS5_TABLE)
    except Exception:  # noqa: BLE001 - FTS5 not compiled in; fall back to LIKE
        logger.warning(
            "SQLite FTS5 unavailable; episodic memory recall will use a LIKE scan"
        )
        return
    for ddl in _FTS5_TRIGGERS:
        conn.exec_driver_sql(ddl)


class Database:
    """Owns the async engine and session factory for one application instance."""

    def __init__(self, url: str) -> None:
        self._url = url
        self._ensure_sqlite_dir(url)
        self._engine: AsyncEngine = create_async_engine(
            url,
            echo=False,
            future=True,
        )
        # Register PRAGMAs on the underlying sync DBAPI connection.
        if url.startswith("sqlite"):
            event.listen(self._engine.sync_engine, "connect", _apply_sqlite_pragmas)

        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            bind=self._engine,
            expire_on_commit=False,
            class_=AsyncSession,
        )

    @staticmethod
    def _ensure_sqlite_dir(url: str) -> None:
        """Create the parent directory for a file-backed SQLite database."""
        marker = ":///"
        if url.startswith("sqlite") and marker in url:
            path = url.split(marker, 1)[1]
            if path and path != ":memory:":
                directory = os.path.dirname(path)
                if directory:
                    os.makedirs(directory, exist_ok=True)

    @property
    def engine(self) -> AsyncEngine:
        return self._engine

    async def create_all(self) -> None:
        """Create missing tables and apply light column migrations.

        No general-purpose migration tool yet (design doc §7); ``create_all``
        adds new tables and ``_apply_light_migrations`` adds new columns to
        tables that predate them. Both are idempotent.
        """
        async with self._engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.run_sync(_apply_light_migrations)
            await conn.run_sync(_apply_index_migrations)
            await conn.run_sync(_apply_fts5)
            await conn.run_sync(_stamp_schema_meta)
            # Touch the connection so WAL is initialized eagerly.
            await conn.execute(text("SELECT 1"))

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """Yield a session with commit/rollback handling."""
        async with self._session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    async def dispose(self) -> None:
        await self._engine.dispose()
