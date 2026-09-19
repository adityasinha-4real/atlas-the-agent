"""Eval runner: drive each golden deterministically and score it (RFC-0004 §21).

Every golden runs through a real ``RunManager`` with a ``ScriptedGateway`` (no
model, no network), then its ledger is scored against the golden's expected
properties. The runner *measures* — it never alters agent behavior.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository, RunRepository
from atlas.runtime.manager import RunManager
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry
from evals.goldens import GOLDENS, Golden, finish, plan


@dataclass
class EvalResult:
    """The scored outcome of one eval."""

    id: str
    category: str
    passed: bool
    status: str
    model_calls: int
    tool_calls: int
    steps: int
    answer_excerpt: str
    failures: list[str] = field(default_factory=list)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _settings(workdir: Path, name: str, **overrides: object) -> Settings:
    db_path = (workdir / f"{name}.db").as_posix()
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="scripted",
        database_url=f"sqlite+aiosqlite:///{db_path}",
        workspace_dir=str(workdir / "ws"),
        log_level="ERROR",
        **overrides,  # type: ignore[arg-type]
    )


async def _event_types(db: Database, run_id: str) -> list[str]:
    async with db.session() as session:
        events = await EventRepository(session).list_after(run_id, 0)
    return [e.type.value for e in events]


async def run_golden(golden: Golden, workdir: Path) -> EvalResult:
    """Run one golden and score it against its expected properties."""
    db = Database(_settings(workdir, golden.id).database_url)
    await db.create_all()
    try:
        gateway = ScriptedGateway(golden.responses)
        settings = _settings(workdir, golden.id, **golden.settings)
        manager = RunManager(
            db, EventEmitter(db, EventHub()), gateway, _registry(), settings
        )
        view = await manager.create_run(golden.goal)
        await manager.wait_for(view.id)

        async with db.session() as session:
            run = await RunRepository(session).get(view.id)
        types = await _event_types(db, view.id)
        answer = (run.answer if run and run.answer else "") or ""
        model_calls = len(gateway.calls)
        tool_calls = types.count("tool.call")

        failures: list[str] = []
        if run is None:
            failures.append("run row missing")
        elif run.status != golden.expect_status:
            failures.append(f"status {run.status} != {golden.expect_status}")
        wanted = golden.answer_contains
        if wanted and wanted.lower() not in answer.lower():
            failures.append(f"answer missing {wanted!r}")
        for event_type in golden.expect_events:
            if event_type not in types:
                failures.append(f"missing event {event_type}")
        if tool_calls < golden.min_tool_calls:
            failures.append(f"tool_calls {tool_calls} < {golden.min_tool_calls}")
        if golden.max_model_calls is not None and model_calls > golden.max_model_calls:
            failures.append(f"model_calls {model_calls} > {golden.max_model_calls}")

        return EvalResult(
            id=golden.id,
            category=golden.category,
            passed=not failures,
            status=run.status if run else "missing",
            model_calls=model_calls,
            tool_calls=tool_calls,
            steps=model_calls,
            answer_excerpt=answer[:80],
            failures=failures,
        )
    finally:
        await db.dispose()


async def run_memory_lift(workdir: Path) -> EvalResult:
    """Paired eval: run A writes a lesson; a related run B recalls it (memory-lift)."""
    settings = _settings(
        workdir, "memlift", memory_enabled=True, memory_distill_with_llm=False
    )
    db = Database(settings.database_url)
    await db.create_all()
    try:
        emitter = EventEmitter(db, EventHub())

        async def _one(goal: str, answer: str) -> str:
            gateway = ScriptedGateway([plan({"description": goal}), finish(answer)])
            manager = RunManager(db, emitter, gateway, _registry(), settings)
            view = await manager.create_run(goal)
            await manager.wait_for(view.id)
            return view.id

        a_id = await _one(
            "convert kilometers to miles for a road trip", "100 km is about 62 miles"
        )
        b_id = await _one(
            "convert kilometers to miles for a cycling route", "done"
        )

        a_types = await _event_types(db, a_id)
        b_types = await _event_types(db, b_id)
        failures: list[str] = []
        if "memory.written" not in a_types:
            failures.append("run A did not write a memory")
        if "memory.recalled" not in b_types:
            failures.append("run B did not recall A's lesson")

        return EvalResult(
            id="memory_lift",
            category="memory",
            passed=not failures,
            status="done",
            model_calls=0,
            tool_calls=0,
            steps=2,
            answer_excerpt="A writes a lesson; B recalls it",
            failures=failures,
        )
    finally:
        await db.dispose()


async def run_all(workdir: Path) -> list[EvalResult]:
    """Run every golden plus the memory-lift eval. Isolated temp DBs per eval."""
    results: list[EvalResult] = []
    for golden in GOLDENS:
        results.append(await run_golden(golden, workdir / uuid.uuid4().hex))
    results.append(await run_memory_lift(workdir / uuid.uuid4().hex))
    return results
