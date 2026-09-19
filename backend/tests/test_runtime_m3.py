"""End-to-end M3 runs through RunManager with the scripted FakeLLM.

Covers the milestone demo (plan → execute tasks in order → synthesize) plus the
failure and cancellation paths, all deterministic — no model or network.
"""

from __future__ import annotations

import asyncio
import json

from atlas.agent.schemas import RunStatus, TaskStatus
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.persistence.database import Database
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskRepository,
)
from atlas.runtime.manager import RunManager
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _plan(*tasks: dict) -> str:
    return json.dumps({"tasks": list(tasks)})


def _tool_call(tool: str, **arguments: object) -> str:
    return json.dumps(
        {
            "thought": "use tool",
            "action": "tool_call",
            "tool": tool,
            "arguments": arguments,
        }
    )


def _finish(answer: str) -> str:
    return json.dumps({"thought": "done", "action": "finish", "answer": answer})


def _manager(
    database: Database, gateway: ScriptedGateway, **overrides: object
) -> RunManager:
    emitter = EventEmitter(database, EventHub())
    settings = Settings(environment="test", **overrides)  # type: ignore[arg-type]
    return RunManager(database, emitter, gateway, _registry(), settings)


async def _events(database: Database, run_id: str) -> list:
    async with database.session() as session:
        return await EventRepository(session).list_after(run_id, 0)


# --------------------------------------------------------------------------- #
# Happy path: multi-task plan → sequential execution → synthesis
# --------------------------------------------------------------------------- #


async def test_multi_task_plan_executes_and_synthesizes(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan(
                {"description": "Find the population of France.", "suggested_tool": None},
                {"description": "Compute 15% of it.", "suggested_tool": "calculator"},
            ),
            _finish("France has about 68,000,000 people."),  # task 0
            _tool_call("calculator", expression="0.15 * 68000000"),  # task 1
            _finish("10200000"),  # task 1
            "About 10.2 million people.",  # synthesis (2 tasks → real call)
        ]
    )
    manager = _manager(database, gateway)

    view = await manager.create_run("What is 15% of France's population?")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    events = await _events(database, view.id)
    types = [e.type for e in events]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "About 10.2 million people."
    assert [t.status for t in tasks] == [TaskStatus.DONE, TaskStatus.DONE]
    assert tasks[0].output == "France has about 68,000,000 people."

    # Ordered lifecycle: plan → task0 (start→complete) → task1 → answer → done.
    assert types.index(EventType.PLAN_CREATED) < types.index(EventType.TASK_STARTED)
    completed = [i for i, t in enumerate(types) if t == EventType.TASK_COMPLETED]
    assert len(completed) == 2
    started = [i for i, t in enumerate(types) if t == EventType.TASK_STARTED]
    assert started[0] < completed[0] < started[1] < completed[1]
    assert completed[1] < types.index(EventType.ANSWER_COMPLETED)
    assert types[-1] == EventType.RUN_COMPLETED

    # Executor events are attributed to the right task.
    tool_call = next(e for e in events if e.type == EventType.TOOL_CALL)
    assert tool_call.payload["task_id"] == f"{view.id}:1"

    # plan.created carries the ordered task briefs.
    plan_event = next(e for e in events if e.type == EventType.PLAN_CREATED)
    assert [t["index"] for t in plan_event.payload["tasks"]] == [0, 1]

    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)


async def test_single_task_plan_short_circuits_synthesis(database: Database) -> None:
    # One task → synthesis is skipped; the task output is the answer. If the
    # synthesizer made an LLM call the script would be exhausted and raise.
    gateway = ScriptedGateway(
        [
            _plan({"description": "Explain hash maps.", "suggested_tool": None}),
            _finish("A hash map maps keys to values via a hash function."),
        ]
    )
    manager = _manager(database, gateway)

    view = await manager.create_run("Explain hash maps.")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
    assert run.status == RunStatus.DONE.value
    assert run.answer == "A hash map maps keys to values via a hash function."
    assert gateway.remaining == 0  # planner + one executor turn only


# --------------------------------------------------------------------------- #
# Failure paths
# --------------------------------------------------------------------------- #


async def test_invalid_plan_fails_run_during_planning(database: Database) -> None:
    gateway = ScriptedGateway(["not json", "still not json", "nope"])
    manager = _manager(database, gateway, agent_repair_attempts=2)

    view = await manager.create_run("do something")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert tasks == []  # no plan persisted
    assert types[-1] == EventType.RUN_FAILED
    assert EventType.PLAN_CREATED not in types


async def test_task_failure_emits_task_failed_and_fails_run(database: Database) -> None:
    # Task never finishes within one iteration → ExecutorError → task.failed → run FAILED.
    gateway = ScriptedGateway(
        [
            _plan({"description": "loop forever", "suggested_tool": "calculator"}),
            _tool_call("calculator", expression="1+1"),
        ]
    )
    manager = _manager(database, gateway, agent_max_iterations=1)

    view = await manager.create_run("cause a failure")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert tasks[0].status == TaskStatus.FAILED
    assert tasks[0].error
    assert EventType.TASK_FAILED in types
    assert types[-1] == EventType.RUN_FAILED


# --------------------------------------------------------------------------- #
# Cancellation
# --------------------------------------------------------------------------- #


async def test_cancel_during_planning(database: Database) -> None:
    gateway = ScriptedGateway([_plan({"description": "unused"})])
    manager = _manager(database, gateway)

    view = await manager.create_run("goal")
    # create_run schedules the run but does not yield to it; cancel lands before
    # the background task executes, so it is seen during PLANNING.
    await manager.cancel_run(view.id)
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.CANCELLED.value
    assert tasks == []
    assert gateway.remaining == 1  # planner never called
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_after_task_execution(database: Database) -> None:
    # The gateway sets the run's cancel event once task 0's executor has produced
    # output (2nd call). From M4 (Phase 6) cancellation is checked right after the
    # executor returns, so task 0 is CANCELLED (not completed) and the never-started
    # task 1 is SKIPPED — no task is left in a non-terminal state.
    class _CancelAfter(ScriptedGateway):
        cancel: asyncio.Event | None = None

        async def complete(self, messages, *, model=None, temperature=None) -> str:
            out = await super().complete(messages, model=model, temperature=temperature)
            if self.cancel is not None and len(self.calls) >= 2:
                self.cancel.set()
            return out

    gateway = _CancelAfter(
        [
            _plan(
                {"description": "task zero"},
                {"description": "task one"},
            ),
            _finish("task zero done"),  # call #2 → cancel set afterwards
        ]
    )
    manager = _manager(database, gateway)

    view = await manager.create_run("two tasks")
    gateway.cancel = manager._cancels[view.id]
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.CANCELLED.value
    assert tasks[0].status == TaskStatus.CANCELLED  # cancel seen after its executor
    assert tasks[1].status == TaskStatus.SKIPPED  # never started
    assert types.count(EventType.TASK_COMPLETED) == 0  # never completed after cancel
    assert EventType.TASK_CANCELLED in types
    assert EventType.TASK_SKIPPED in types
    assert types[-1] == EventType.RUN_CANCELLED
