"""End-to-end episodic memory through RunManager (M5, RFC-0003).

Recall at plan time, best-effort write at finalization, planner injection, replan
reuse, failure isolation, and byte-for-byte M4 parity when memory is disabled — all
deterministic via the scripted FakeLLM.
"""

from __future__ import annotations

import json

from atlas.agent.schemas import RunStatus
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.memory.schemas import MemoryOutcome, MemoryRecord, MemorySource
from atlas.memory.store import EpisodicStore
from atlas.persistence.database import Database
from atlas.persistence.models import RunRow
from atlas.persistence.repositories import EventRepository, MemoryRepository
from atlas.runtime.manager import RunManager
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry

_LESSONS_HEADER = "Lessons from past runs"


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _plan(*tasks: dict) -> str:
    return json.dumps({"tasks": list(tasks)})


def _tool_call(tool: str, **arguments: object) -> str:
    return json.dumps(
        {"thought": "t", "action": "tool_call", "tool": tool, "arguments": arguments}
    )


def _finish(answer: str) -> str:
    return json.dumps({"thought": "done", "action": "finish", "answer": answer})


def _reflect(decision: str, reason: str = "ok", confidence: float = 0.9) -> str:
    return json.dumps(
        {"decision": decision, "reason": reason, "confidence": confidence}
    )


def _manager(
    database: Database, gateway: ScriptedGateway, **overrides: object
) -> RunManager:
    emitter = EventEmitter(database, EventHub())
    settings = Settings(environment="test", **overrides)  # type: ignore[arg-type]
    return RunManager(database, emitter, gateway, _registry(), settings)


async def _events(database: Database, run_id: str) -> list:
    async with database.session() as session:
        return await EventRepository(session).list_after(run_id, 0)


async def _memories(database: Database) -> list:
    async with database.session() as session:
        return await MemoryRepository(session).list_recent(limit=50)


async def _seed(
    database: Database,
    *,
    goal: str,
    lessons: str,
    run_id: str | None = None,
    tools: list[str] | None = None,
) -> None:
    await EpisodicStore(database).write(
        MemoryRecord(
            run_id=run_id,
            goal=goal,
            outcome=MemoryOutcome.DONE,
            summary="prior run summary",
            lessons=lessons,
            tools_used=tools or ["calculator"],
            task_count=2,
            source=MemorySource.HEURISTIC,
        )
    )


# --------------------------------------------------------------------------- #
# Write path
# --------------------------------------------------------------------------- #


async def test_memory_written_on_done(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Find the population.", "suggested_tool": None},
                {"description": "Compute a percentage.", "suggested_tool": "calculator"},
            ),
            _finish("about 68 million"),
            _tool_call("calculator", expression="0.1 * 68000000"),
            _finish("6800000"),
            "final answer",
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=False
    )
    view = await manager.create_run("What is 10% of the population?")
    await manager.wait_for(view.id)

    memories = await _memories(database)
    assert len(memories) == 1
    mem = memories[0]
    assert mem.outcome is MemoryOutcome.DONE
    assert mem.run_id == view.id
    assert "calculator" in mem.tools_used  # gathered from tool.call events
    assert mem.source is MemorySource.HEURISTIC

    types = [e.type for e in await _events(database, view.id)]
    assert EventType.MEMORY_WRITTEN in types
    # Written only after the answer is delivered (RFC-0003 §13).
    assert types.index(EventType.ANSWER_COMPLETED) < types.index(
        EventType.MEMORY_WRITTEN
    )


async def test_llm_distillation_produces_synthesized_memory(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Step one.", "suggested_tool": None},
                {"description": "Step two.", "suggested_tool": None},
            ),
            _finish("out0"),
            _finish("out1"),
            "the final answer",  # synthesis (2 outputs)
            json.dumps({"summary": "did two steps", "lessons": "split into two steps"}),
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=True
    )
    view = await manager.create_run("A two-step goal")
    await manager.wait_for(view.id)

    memories = await _memories(database)
    assert len(memories) == 1
    assert memories[0].source is MemorySource.SYNTHESIZED
    assert memories[0].summary == "did two steps"
    assert memories[0].lessons == "split into two steps"


async def test_min_tasks_skips_trivial_run(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "Only one task.", "suggested_tool": None}),
            _finish("the answer"),
        ]
    )
    manager = _manager(
        database,
        gateway,
        memory_enabled=True,
        memory_distill_with_llm=False,
        memory_min_tasks=2,
    )
    view = await manager.create_run("A trivial goal")
    await manager.wait_for(view.id)

    assert await _memories(database) == []
    types = [e.type for e in await _events(database, view.id)]
    assert EventType.MEMORY_WRITTEN not in types


async def test_memory_written_on_graceful_partial(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Task A.", "suggested_tool": None},
                {"description": "Task B.", "suggested_tool": None},
            ),
            _finish("output A"),
            _reflect("accept"),
            _finish("output B"),
            _reflect("abort", reason="cannot finish"),
        ]
    )
    manager = _manager(
        database,
        gateway,
        memory_enabled=True,
        memory_distill_with_llm=False,
        agent_enable_reflection=True,
        agent_max_replans=0,
    )
    view = await manager.create_run("A goal that aborts")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await session.get(RunRow, view.id)
    assert run.status == RunStatus.FAILED.value
    memories = await _memories(database)
    assert len(memories) == 1
    assert memories[0].outcome is MemoryOutcome.PARTIAL


# --------------------------------------------------------------------------- #
# Recall path
# --------------------------------------------------------------------------- #


async def test_recall_injects_lessons_into_planner(database: Database) -> None:
    await _seed(
        database,
        goal="convert a distance in kilometers to miles",
        lessons="use the calculator tool to multiply by 0.621371",
    )
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Convert the distance.", "suggested_tool": "calculator"}
            ),
            _finish("42 miles"),
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=False
    )
    view = await manager.create_run("convert kilometers to miles for a trip")
    await manager.wait_for(view.id)

    planner_system = gateway.calls[0][0].content
    assert _LESSONS_HEADER in planner_system
    assert "0.621371" in planner_system

    recalled = [
        e for e in await _events(database, view.id) if e.type == EventType.MEMORY_RECALLED
    ]
    assert len(recalled) == 1
    assert recalled[0].payload["count"] == 1
    assert _LESSONS_HEADER in recalled[0].payload["rendered"]


async def test_recall_empty_for_unrelated_goal(database: Database) -> None:
    await _seed(
        database,
        goal="bake a chocolate cake",
        lessons="preheat the oven",
    )
    gateway = ScriptedGateway(
        [
            _plan({"description": "Explain a concept.", "suggested_tool": None}),
            _finish("an explanation"),
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=False
    )
    view = await manager.create_run("explain quantum entanglement physics")
    await manager.wait_for(view.id)

    planner_system = gateway.calls[0][0].content
    assert _LESSONS_HEADER not in planner_system
    types = [e.type for e in await _events(database, view.id)]
    assert EventType.MEMORY_RECALLED not in types


async def test_cross_run_learning(database: Database) -> None:
    # Run A learns a lesson.
    gateway_a = ScriptedGateway(
        [
            _plan(
                {"description": "Find the height in meters.", "suggested_tool": None},
                {
                    "description": "Convert meters to feet.",
                    "suggested_tool": "calculator",
                },
            ),
            _finish("8849 meters"),
            _tool_call("calculator", expression="8849 * 3.28084"),
            _finish("29032 feet"),
            "The height is about 29032 feet.",
        ]
    )
    manager_a = _manager(
        database, gateway_a, memory_enabled=True, memory_distill_with_llm=False
    )
    run_a = await manager_a.create_run("convert the height of Everest in meters to feet")
    await manager_a.wait_for(run_a.id)
    assert len(await _memories(database)) == 1

    # Run B on a related goal recalls run A's memory.
    gateway_b = ScriptedGateway(
        [
            _plan({"description": "Convert the depth.", "suggested_tool": "calculator"}),
            _finish("about 36000 feet"),
        ]
    )
    manager_b = _manager(
        database, gateway_b, memory_enabled=True, memory_distill_with_llm=False
    )
    run_b = await manager_b.create_run(
        "convert the depth of the trench in meters to feet"
    )
    await manager_b.wait_for(run_b.id)

    events_b = await _events(database, run_b.id)
    recalled = [e for e in events_b if e.type == EventType.MEMORY_RECALLED]
    assert len(recalled) == 1
    assert recalled[0].payload["count"] >= 1
    assert _LESSONS_HEADER in gateway_b.calls[0][0].content


async def test_replan_reuses_recalled_lessons(database: Database) -> None:
    await _seed(
        database,
        goal="compute a metric conversion with the calculator",
        lessons="use the calculator tool for every conversion step",
    )
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "First conversion.", "suggested_tool": "calculator"},
                {"description": "Second conversion.", "suggested_tool": "calculator"},
            ),
            _finish("first result"),
            _reflect("accept"),
            _finish("second result"),
            _reflect("replan", reason="approach wrong for the rest"),
            _plan({"description": "Redone conversion.", "suggested_tool": "calculator"}),
            _finish("redone result"),
            _reflect("accept"),
            "the synthesized answer",
        ]
    )
    manager = _manager(
        database,
        gateway,
        memory_enabled=True,
        memory_distill_with_llm=False,
        agent_enable_reflection=True,
        agent_max_replans=1,
    )
    view = await manager.create_run("compute a metric conversion for the report")
    await manager.wait_for(view.id)

    # Both the initial plan and the replan carry the recalled lessons (RFC-0003 §12).
    planner_prompts = [
        call[0].content for call in gateway.calls if _LESSONS_HEADER in call[0].content
    ]
    assert len(planner_prompts) >= 2


# --------------------------------------------------------------------------- #
# Disabled parity + failure isolation
# --------------------------------------------------------------------------- #


async def test_disabled_memory_is_m4_parity(database: Database) -> None:
    await _seed(database, goal="a goal about miles and kilometers", lessons="a lesson")
    gateway = ScriptedGateway(
        [
            _plan({"description": "Do the thing.", "suggested_tool": None}),
            _finish("the answer"),
        ]
    )
    manager = _manager(database, gateway, memory_enabled=False)
    view = await manager.create_run("a goal about miles and kilometers conversion")
    await manager.wait_for(view.id)

    # No recall (planner prompt untouched), no memory events, no new memory written.
    assert _LESSONS_HEADER not in gateway.calls[0][0].content
    types = [e.type for e in await _events(database, view.id)]
    assert EventType.MEMORY_RECALLED not in types
    assert EventType.MEMORY_WRITTEN not in types
    assert len(await _memories(database)) == 1  # only the seeded one; none added


async def test_write_failure_never_affects_run(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Step one.", "suggested_tool": None},
                {"description": "Step two.", "suggested_tool": None},
            ),
            _finish("out0"),
            _finish("out1"),
            "final answer",
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=False
    )

    async def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("store is down")

    manager._memory._store.write = _boom  # type: ignore[attr-defined]

    view = await manager.create_run("a resilient goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await session.get(RunRow, view.id)
    assert run.status == RunStatus.DONE.value
    assert run.answer == "final answer"
    types = [e.type for e in await _events(database, view.id)]
    assert EventType.RUN_COMPLETED in types
    assert EventType.MEMORY_WRITTEN not in types  # write failed, silently


async def test_recall_failure_never_affects_run(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "Do the thing.", "suggested_tool": None}),
            _finish("the answer"),
        ]
    )
    manager = _manager(
        database, gateway, memory_enabled=True, memory_distill_with_llm=False
    )

    async def _boom(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("recall is down")

    manager._memory._store.recall = _boom  # type: ignore[attr-defined]

    view = await manager.create_run("a goal that recalls despite failure")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await session.get(RunRow, view.id)
    assert run.status == RunStatus.DONE.value
    assert _LESSONS_HEADER not in gateway.calls[0][0].content
