"""End-to-end M2 tool-loop run through RunManager with the scripted FakeLLM.

Proves the tool-loop demo deterministically: a goal drives a think → tool_call →
tool_result → answer event sequence, the answer is streamed and persisted, and
the run reaches DONE — no model or network involved.

Migrated for M3: runs now plan first, so the script opens with a single-task
planner response (synthesis short-circuits for a one-task plan, RFC-0001
decision 1). The tool-loop assertions are unchanged.
"""

from __future__ import annotations

import json

from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository, RunRepository
from atlas.runtime.manager import RunManager
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry


async def test_m2_demo_flow_search_compute_answer(database: Database) -> None:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    emitter = EventEmitter(database, EventHub())
    gateway = ScriptedGateway(
        [
            # 1) Planner: a single task (synthesis short-circuits).
            json.dumps(
                {
                    "tasks": [
                        {
                            "description": "Compute 15% of France's population.",
                            "success_criteria": "The product is known.",
                            "suggested_tool": "calculator",
                        }
                    ]
                }
            ),
            # 2) Executor: call the calculator.
            json.dumps(
                {
                    "thought": "France has ~68M people; compute 15%.",
                    "action": "tool_call",
                    "tool": "calculator",
                    "arguments": {"expression": "0.15 * 68000000"},
                }
            ),
            # 3) Executor: finish with the task result.
            json.dumps(
                {
                    "thought": "That is the answer.",
                    "action": "finish",
                    "answer": "About 10,200,000 people.",
                }
            ),
        ]
    )
    manager = RunManager(
        database, emitter, gateway, registry, Settings(environment="test")
    )

    view = await manager.create_run("What is 15% of France's population?")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        events = await EventRepository(session).list_after(view.id, 0)

    assert run.status == "done"
    assert run.answer == "About 10,200,000 people."

    types = [e.type for e in events]
    # Ordered agentic sequence.
    assert types.index(EventType.THOUGHT) < types.index(EventType.TOOL_CALL)
    assert types.index(EventType.TOOL_CALL) < types.index(EventType.TOOL_RESULT)
    assert types.index(EventType.TOOL_RESULT) < types.index(EventType.ANSWER_COMPLETED)
    assert types[-1] == EventType.RUN_COMPLETED

    tool_result = next(e for e in events if e.type == EventType.TOOL_RESULT)
    assert tool_result.payload["observation"] == "10200000"

    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
