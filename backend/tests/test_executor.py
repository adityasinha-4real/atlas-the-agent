"""The executor ReAct loop, driven by the scripted FakeLLM (no model/network)."""

from __future__ import annotations

import asyncio
import json

import pytest

from atlas.agent.executor import Executor, ExecutorError, RunCancelled
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.gateway import LLMMessage
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository, RunRepository
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry


def _tool_call(tool: str, **arguments: object) -> str:
    return json.dumps(
        {"thought": "next", "action": "tool_call", "tool": tool, "arguments": arguments}
    )


def _finish(answer: str) -> str:
    return json.dumps({"thought": "done", "action": "finish", "answer": answer})


def _msgs(goal: str) -> list[LLMMessage]:
    """A minimal prebuilt context; the scripted gateway ignores its content."""
    return [
        LLMMessage(role="system", content="test system prompt"),
        LLMMessage(role="user", content=f"Goal: {goal}"),
    ]


async def _make(
    database: Database, responses: list[str], **overrides: object
) -> tuple[Executor, EventEmitter, str]:
    run_id = "run-exec"
    async with database.session() as session:
        await RunRepository(session).create(run_id, "goal")
    emitter = EventEmitter(database, EventHub())
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    settings = Settings(environment="test", **overrides)  # type: ignore[arg-type]
    gateway = ScriptedGateway(responses)
    return Executor(gateway, registry, emitter, settings), emitter, run_id


async def _types(database: Database, run_id: str) -> list[EventType]:
    async with database.session() as session:
        events = await EventRepository(session).list_after(run_id, 0)
    return [e.type for e in events]


async def test_tool_call_then_finish(database: Database) -> None:
    executor, _, run_id = await _make(
        database,
        [_tool_call("calculator", expression="0.15 * 68000000"), _finish("10.2M")],
    )
    answer = await executor.run(run_id, _msgs("15% of France"), asyncio.Event())
    assert answer == "10.2M"

    types = await _types(database, run_id)
    assert EventType.THOUGHT in types
    assert EventType.TOOL_CALL in types
    assert EventType.TOOL_RESULT in types


async def test_tool_result_observation_is_emitted(database: Database) -> None:
    executor, _, run_id = await _make(
        database, [_tool_call("calculator", expression="2+2"), _finish("four")]
    )
    await executor.run(run_id, _msgs("add"), asyncio.Event())
    async with database.session() as session:
        events = await EventRepository(session).list_after(run_id, 0)
    tool_results = [e for e in events if e.type == EventType.TOOL_RESULT]
    assert tool_results and tool_results[0].payload["observation"] == "4"
    assert tool_results[0].payload["ok"] is True


async def test_repair_loop_recovers_from_malformed_json(database: Database) -> None:
    executor, _, run_id = await _make(
        database,
        ["not json at all", _finish("recovered")],
        agent_repair_attempts=2,
    )
    answer = await executor.run(run_id, _msgs("q"), asyncio.Event())
    assert answer == "recovered"


async def test_unparseable_after_repair_degrades_to_answer(database: Database) -> None:
    executor, _, run_id = await _make(
        database,
        ["nope", "still nope", "really not json"],
        agent_repair_attempts=2,
    )
    answer = await executor.run(run_id, _msgs("q"), asyncio.Event())
    # Degrades to the model's *first* response, not the last repair attempt.
    assert answer == "nope"


async def test_iteration_budget_exhaustion_raises(database: Database) -> None:
    executor, _, run_id = await _make(
        database,
        [_tool_call("calculator", expression="1+1")] * 2,
        agent_max_iterations=2,
    )
    with pytest.raises(ExecutorError):
        await executor.run(run_id, _msgs("loop"), asyncio.Event())


async def test_cancellation_is_cooperative(database: Database) -> None:
    executor, _, run_id = await _make(database, [_finish("unused")])
    cancel = asyncio.Event()
    cancel.set()
    with pytest.raises(RunCancelled):
        await executor.run(run_id, _msgs("q"), cancel)


async def test_bad_tool_args_surface_as_observation_not_crash(database: Database) -> None:
    # First the model calls a tool with invalid args; it should get an error
    # observation and be able to finish on the next turn.
    executor, _, run_id = await _make(
        database,
        [_tool_call("calculator", wrong="x"), _finish("handled")],
    )
    answer = await executor.run(run_id, _msgs("q"), asyncio.Event())
    assert answer == "handled"
    async with database.session() as session:
        events = await EventRepository(session).list_after(run_id, 0)
    result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert result.payload["ok"] is False
