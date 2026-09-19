"""Planner behavior: parsing, normalization, clamping, and failure."""

from __future__ import annotations

import json

import pytest

from atlas.agent.planner import Planner, PlannerError
from atlas.core.config import Settings
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry


def _settings(**overrides) -> Settings:
    return Settings(environment="test", **overrides)


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _plan_json(tasks: list[dict]) -> str:
    return json.dumps({"tasks": tasks})


async def test_parses_valid_plan() -> None:
    gateway = ScriptedGateway(
        [
            _plan_json(
                [
                    {
                        "description": "Find the population of France.",
                        "success_criteria": "A population figure is known.",
                        "suggested_tool": "web_search",
                    },
                    {
                        "description": "Compute 15% of it.",
                        "success_criteria": "The product is known.",
                        "suggested_tool": "calculator",
                    },
                ]
            )
        ]
    )
    # Only calculator is registered; web_search is intentionally absent so the
    # planner nulls that hint.
    planner = Planner(gateway, _registry(), _settings())

    tasks = await planner.plan("What is 15% of France's population?")

    assert [t.description for t in tasks] == [
        "Find the population of France.",
        "Compute 15% of it.",
    ]
    # web_search is not registered here → hint nulled; calculator kept.
    assert tasks[0].suggested_tool is None
    assert tasks[1].suggested_tool == "calculator"


async def test_clamps_to_max_tasks() -> None:
    tasks = [{"description": f"task {i}"} for i in range(9)]
    gateway = ScriptedGateway([_plan_json(tasks)])
    planner = Planner(gateway, _registry(), _settings(agent_max_tasks=5))

    result = await planner.plan("do many things")

    assert len(result) == 5
    assert [t.description for t in result] == [f"task {i}" for i in range(5)]


async def test_drops_empty_description_tasks() -> None:
    gateway = ScriptedGateway(
        [
            _plan_json(
                [
                    {"description": "   "},
                    {"description": "real task"},
                    {"description": ""},
                ]
            )
        ]
    )
    planner = Planner(gateway, _registry(), _settings())

    result = await planner.plan("goal")

    assert [t.description for t in result] == ["real task"]


async def test_nulls_unknown_suggested_tool() -> None:
    gateway = ScriptedGateway(
        [_plan_json([{"description": "x", "suggested_tool": "rm_rf"}])]
    )
    planner = Planner(gateway, _registry(), _settings())

    result = await planner.plan("goal")

    assert result[0].suggested_tool is None


async def test_empty_plan_falls_back_to_goal() -> None:
    gateway = ScriptedGateway([_plan_json([])])
    planner = Planner(gateway, _registry(), _settings())

    result = await planner.plan("just answer this")

    assert len(result) == 1
    assert result[0].description == "just answer this"


async def test_invalid_after_repair_raises() -> None:
    # Every response is unparseable; repair budget exhausts → PlannerError.
    gateway = ScriptedGateway(["not json", "still not json", "nope"])
    planner = Planner(gateway, _registry(), _settings(agent_repair_attempts=2))

    with pytest.raises(PlannerError):
        await planner.plan("goal")

    assert gateway.remaining == 0  # used first + 2 repair attempts


async def test_repairs_then_succeeds() -> None:
    gateway = ScriptedGateway(
        ["garbage", _plan_json([{"description": "recovered task"}])]
    )
    planner = Planner(gateway, _registry(), _settings(agent_repair_attempts=2))

    result = await planner.plan("goal")

    assert result[0].description == "recovered task"
    assert gateway.remaining == 0
