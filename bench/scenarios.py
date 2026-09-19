"""Deterministic benchmark scenarios (RFC-0004 §4-8, §15 timing).

Each scenario builds its own temp SQLite database, seeds deterministic fixtures,
and times a single hot operation many times. They cover the areas the RFC calls
out: event emission, event-stream backfill/replay read, FTS memory recall, the
runs-list query, and a full echo run end-to-end.

Scenarios take a ``workdir`` and return a :class:`Result` with raw samples. They
are plain coroutines so the CI subset can run just a few of them.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter

from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.gateway import build_gateway
from atlas.memory.schemas import MemoryOutcome, MemoryRecord, MemorySource
from atlas.persistence.database import Database
from atlas.persistence.repositories import (
    EventRepository,
    MemoryRepository,
    RunRepository,
)
from atlas.tools import build_registry
from bench._harness import Result

_WORDS = ["alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel"]


def _settings(workdir: Path, name: str) -> Settings:
    db_path = (workdir / f"{name}.db").as_posix()
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        workspace_dir=str(workdir / "ws"),
        log_level="ERROR",
    )


async def _fresh_db(workdir: Path, name: str) -> Database:
    db = Database(_settings(workdir, name).database_url)
    await db.create_all()
    return db


async def _explain(db: Database, sql: str) -> str:
    """Return a one-line summary of a query plan (justification for §7/§9)."""
    async with db.engine.connect() as conn:
        result = await conn.exec_driver_sql(f"EXPLAIN QUERY PLAN {sql}")
        details = [str(row[-1]) for row in result.fetchall()]
    return " | ".join(details)


# --------------------------------------------------------------------------- #
# Scenarios
# --------------------------------------------------------------------------- #


async def emit_event(workdir: Path, *, n: int = 300) -> Result:
    """Latency of a single ``emit()`` (next_seq + insert + publish)."""
    db = await _fresh_db(workdir, "emit")
    try:
        hub = EventHub()
        emitter = EventEmitter(db, hub)
        run_id = uuid.uuid4().hex
        async with db.session() as session:
            await RunRepository(session).create(run_id, "bench emit")
        samples: list[float] = []
        for i in range(n):
            start = perf_counter()
            await emitter.emit(run_id, EventType.LOG, {"i": i})
            samples.append(perf_counter() - start)
        return Result("emit_event", "s/op", samples)
    finally:
        await db.dispose()


async def event_backfill_replay(
    workdir: Path, *, events: int = 800, reps: int = 60
) -> Result:
    """Latency of loading a full ledger (``list_after``) — the replay read path."""
    db = await _fresh_db(workdir, "backfill")
    try:
        run_id = uuid.uuid4().hex
        async with db.session() as session:
            await RunRepository(session).create(run_id, "bench backfill")
            repo = EventRepository(session)
            for seq in range(1, events + 1):
                await repo.append(run_id, seq, EventType.LOG, {"i": seq})
        samples: list[float] = []
        for _ in range(reps):
            async with db.session() as session:
                start = perf_counter()
                loaded = await EventRepository(session).list_after(run_id, 0)
                samples.append(perf_counter() - start)
            assert len(loaded) == events
        plan = await _explain(
            db, f"SELECT * FROM events WHERE run_id = '{run_id}' AND seq > 0 "
            "ORDER BY seq ASC"
        )
        return Result(
            "event_backfill_replay", "s/scan", samples,
            meta={"events": events, "query_plan": plan},
        )
    finally:
        await db.dispose()


async def memory_recall_fts(
    workdir: Path, *, memories: int = 400, reps: int = 100
) -> Result:
    """Latency of an FTS5 recall query over a seeded memory store."""
    db = await _fresh_db(workdir, "recall")
    try:
        async with db.session() as session:
            repo = MemoryRepository(session)
            for i in range(memories):
                word = _WORDS[i % len(_WORDS)]
                await repo.create(
                    MemoryRecord(
                        run_id=None,
                        goal=f"convert measurement {i} of {word} to metric",
                        outcome=MemoryOutcome.DONE,
                        summary=f"converted {word} number {i}",
                        lessons=f"use the calculator for {word} conversions",
                        tools_used=["calculator"],
                        task_count=2,
                        source=MemorySource.HEURISTIC,
                    )
                )
        query = "convert measurement 42 of gamma to metric"
        samples: list[float] = []
        for _ in range(reps):
            async with db.session() as session:
                start = perf_counter()
                await MemoryRepository(session).search(query, limit=5)
                samples.append(perf_counter() - start)
        return Result(
            "memory_recall_fts", "s/query", samples, meta={"memories": memories}
        )
    finally:
        await db.dispose()


async def runs_list(workdir: Path, *, runs: int = 3000, reps: int = 80) -> Result:
    """Latency of the ``GET /runs`` list query (newest-first, limit 50)."""
    db = await _fresh_db(workdir, "runslist")
    try:
        async with db.session() as session:
            repo = RunRepository(session)
            for i in range(runs):
                await repo.create(uuid.uuid4().hex, f"benchmark goal number {i}")
        samples: list[float] = []
        for _ in range(reps):
            async with db.session() as session:
                start = perf_counter()
                rows = await RunRepository(session).list_recent(limit=50)
                samples.append(perf_counter() - start)
            assert len(rows) == 50
        plan = await _explain(db, "SELECT * FROM runs ORDER BY created_at DESC LIMIT 50")
        return Result(
            "runs_list", "s/query", samples,
            meta={"runs": runs, "query_plan": plan},
        )
    finally:
        await db.dispose()


async def run_end_to_end(workdir: Path, *, reps: int = 20) -> Result:
    """End-to-end latency of a full echo run (create → terminal)."""
    from atlas.runtime.manager import RunManager

    settings = _settings(workdir, "e2e")
    db = Database(settings.database_url)
    await db.create_all()
    gateway = build_gateway(settings)
    try:
        hub = EventHub()
        emitter = EventEmitter(db, hub)
        registry = build_registry(settings)
        manager = RunManager(db, emitter, gateway, registry, settings)
        samples: list[float] = []
        for i in range(reps):
            start = perf_counter()
            view = await manager.create_run(f"echo benchmark goal {i}")
            await manager.wait_for(view.id)
            samples.append(perf_counter() - start)
        return Result("run_end_to_end_echo", "s/run", samples)
    finally:
        await gateway.aclose()
        await db.dispose()


# Registry of scenarios. ``ci`` marks the short subset run in CI (RFC-0004 §20).
Scenario = Callable[..., Awaitable[Result]]

ALL: dict[str, Scenario] = {
    "emit_event": emit_event,
    "event_backfill_replay": event_backfill_replay,
    "memory_recall_fts": memory_recall_fts,
    "runs_list": runs_list,
    "run_end_to_end": run_end_to_end,
}

CI_SUBSET: tuple[str, ...] = ("emit_event", "runs_list", "run_end_to_end")
