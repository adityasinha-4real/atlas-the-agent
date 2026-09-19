"""The ``MemoryStore`` seam and its episodic (FTS5) implementation (RFC-0003 §2).

``MemoryStore`` is the single boundary every memory read/write crosses, so a
future semantic (FAISS) tier is a drop-in with the same contract
([ADR-0004](../../docs/adr/0004-fts5-over-faiss.md), invariant I-21). ``EpisodicStore``
backs it with SQLite: retrieval via ``MemoryRepository`` (FTS5 with a LIKE
fallback) and ranking/selection via :mod:`atlas.memory.ranking`.
"""

from __future__ import annotations

import abc
from datetime import UTC, datetime

from atlas.memory.ranking import score_candidates, select_within_budget
from atlas.memory.schemas import MemoryRecord, MemoryStatus, MemoryView, RecalledMemory
from atlas.persistence.database import Database
from atlas.persistence.repositories import MemoryRepository


def _utcnow() -> datetime:
    return datetime.now(UTC)


class MemoryStore(abc.ABC):
    """Provider-agnostic long-term memory. Implementations must be async."""

    @abc.abstractmethod
    async def recall(
        self, goal: str, *, k: int, char_budget: int, min_score: float
    ) -> list[RecalledMemory]:
        """Return up to ``k`` relevant, budgeted lessons for ``goal``."""
        raise NotImplementedError

    @abc.abstractmethod
    async def write(self, record: MemoryRecord) -> MemoryView:
        """Persist (upsert) a distilled memory and return its view."""
        raise NotImplementedError

    @abc.abstractmethod
    async def touch(self, memory_ids: list[str]) -> None:
        """Bump recall salience/usage for the given memories."""
        raise NotImplementedError

    @abc.abstractmethod
    async def prune(self, *, max_records: int, expiry_days: int) -> list[str]:
        """Enforce the store bound/expiry; return soft-expired ids."""
        raise NotImplementedError

    @abc.abstractmethod
    async def list_recent(self, *, limit: int, offset: int) -> list[MemoryView]:
        raise NotImplementedError

    @abc.abstractmethod
    async def search(self, query: str, *, limit: int) -> list[MemoryView]:
        """Relevance-ranked lookup for the management API (no budget/truncation)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get(self, memory_id: str) -> MemoryView | None:
        raise NotImplementedError

    @abc.abstractmethod
    async def set_pinned(self, memory_id: str, pinned: bool) -> MemoryView | None:
        raise NotImplementedError

    @abc.abstractmethod
    async def delete(self, memory_id: str) -> bool:
        raise NotImplementedError

    @abc.abstractmethod
    async def purge(self) -> int:
        raise NotImplementedError


class EpisodicStore(MemoryStore):
    """SQLite/FTS5-backed episodic memory (RFC-0003 §4)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def recall(
        self, goal: str, *, k: int, char_budget: int, min_score: float
    ) -> list[RecalledMemory]:
        if k <= 0:
            return []
        # Over-fetch candidates, then rank/select deterministically.
        async with self._db.session() as session:
            candidates = await MemoryRepository(session).search(goal, limit=k * 3)
        if not candidates:
            return []
        scored = score_candidates(goal, candidates, now=_utcnow())
        return select_within_budget(
            scored, k=k, char_budget=char_budget, min_score=min_score
        )

    async def write(self, record: MemoryRecord) -> MemoryView:
        async with self._db.session() as session:
            return await MemoryRepository(session).create(record)

    async def touch(self, memory_ids: list[str]) -> None:
        if not memory_ids:
            return
        async with self._db.session() as session:
            await MemoryRepository(session).touch(memory_ids, when=_utcnow())

    async def prune(self, *, max_records: int, expiry_days: int) -> list[str]:
        async with self._db.session() as session:
            return await MemoryRepository(session).prune(
                max_records=max_records, expiry_days=expiry_days, now=_utcnow()
            )

    async def list_recent(
        self, *, limit: int, offset: int = 0
    ) -> list[MemoryView]:
        async with self._db.session() as session:
            return await MemoryRepository(session).list_recent(
                limit=limit, offset=offset, status=MemoryStatus.ACTIVE
            )

    async def search(self, query: str, *, limit: int) -> list[MemoryView]:
        async with self._db.session() as session:
            return await MemoryRepository(session).search(query, limit=limit)

    async def get(self, memory_id: str) -> MemoryView | None:
        async with self._db.session() as session:
            return await MemoryRepository(session).get(memory_id)

    async def set_pinned(self, memory_id: str, pinned: bool) -> MemoryView | None:
        async with self._db.session() as session:
            return await MemoryRepository(session).set_pinned(memory_id, pinned)

    async def delete(self, memory_id: str) -> bool:
        async with self._db.session() as session:
            return await MemoryRepository(session).delete(memory_id)

    async def purge(self) -> int:
        async with self._db.session() as session:
            return await MemoryRepository(session).purge()
