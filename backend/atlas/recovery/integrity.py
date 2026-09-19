"""Persistence integrity checks (RFC-0004 §14).

Opt-in (``ATLAS_DB_INTEGRITY_CHECK``, default off to keep startup fast). Runs
SQLite's ``quick_check`` and a lightweight FTS-drift check (does the ``memories_fts``
index still mirror ``memories``?). On a hard failure it raises so the app fails
fast rather than serving from a corrupt store; drift is reported, not fatal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import text

from atlas.persistence.database import Database

logger = logging.getLogger(__name__)


class IntegrityError(RuntimeError):
    """Raised when the database fails ``PRAGMA quick_check`` (fail-fast)."""


@dataclass
class IntegrityReport:
    """Outcome of a startup integrity check."""

    quick_check_ok: bool
    fts_in_sync: bool
    detail: str = "ok"

    @property
    def healthy(self) -> bool:
        return self.quick_check_ok and self.fts_in_sync


async def check_integrity(
    db: Database, *, raise_on_error: bool = True
) -> IntegrityReport:
    """Run ``quick_check`` + an FTS-drift check. Raises on corruption if asked."""
    async with db.engine.connect() as conn:
        result = await conn.exec_driver_sql("PRAGMA quick_check")
        rows = [str(r[0]) for r in result.fetchall()]
    quick_ok = rows == ["ok"]

    fts_in_sync = True
    try:
        async with db.engine.connect() as conn:
            memories = (
                await conn.exec_driver_sql("SELECT count(*) FROM memories")
            ).scalar_one()
            indexed = (
                await conn.exec_driver_sql("SELECT count(*) FROM memories_fts")
            ).scalar_one()
        fts_in_sync = int(memories) == int(indexed)
    except Exception:  # noqa: BLE001 - FTS5 absent (LIKE fallback); not a drift error
        fts_in_sync = True

    detail = "ok" if quick_ok else "; ".join(rows) or "quick_check failed"
    report = IntegrityReport(
        quick_check_ok=quick_ok, fts_in_sync=fts_in_sync, detail=detail
    )
    if not report.quick_check_ok:
        logger.error("database integrity check FAILED: %s", detail)
        if raise_on_error:
            raise IntegrityError(detail)
    elif not report.fts_in_sync:
        logger.warning("memories_fts index drift detected; consider a rebuild")
    return report


async def read_schema_meta(db: Database) -> dict[str, str]:
    """Read the ``schema_meta`` key/value stamp (M6 §14)."""
    async with db.engine.connect() as conn:
        result = await conn.execute(text("SELECT key, value FROM schema_meta"))
        return {row[0]: row[1] for row in result.fetchall()}
