"""RunBudget counting/caps/inspection and the gateway/registry wrappers.

The wrappers are exercised against a ScriptedGateway and a real ToolRegistry with
a trivial tool; the key invariant under test is that a BudgetExceeded is charged
*before* the wrapped I/O and propagates (never trapped as a tool observation).
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from atlas.core.config import Settings
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.runtime.budget import (
    BudgetCategory,
    BudgetedGateway,
    BudgetedToolRegistry,
    BudgetExceeded,
    RunBudget,
)
from atlas.tools.base import Tool, ToolResult
from atlas.tools.registry import ToolRegistry


def _budget(**overrides: int) -> RunBudget:
    base = dict(max_model_calls=60, max_tool_calls=40, max_retries=2, max_replans=1)
    base.update(overrides)
    return RunBudget(**base)  # type: ignore[arg-type]


class _PingTool(Tool):
    name = "ping"
    description = "returns pong"

    class Args(BaseModel):
        pass

    async def run(self, args: BaseModel) -> ToolResult:
        return ToolResult.success("pong")


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_PingTool())
    return reg


# --- RunBudget: counting --------------------------------------------------- #


def test_charge_model_call_bumps_total_and_category() -> None:
    budget = _budget()
    budget.charge_model_call(BudgetCategory.PLANNER)
    budget.charge_model_call(BudgetCategory.REFLECTION)
    budget.charge_model_call(BudgetCategory.SYNTHESIS)
    budget.charge_model_call(BudgetCategory.EXECUTOR)

    assert budget.model_calls == 4
    assert budget.planner_calls == 1
    assert budget.reflection_calls == 1
    assert budget.synthesis_calls == 1
    # Executor is the implicit remainder: no named counter.
    executor_calls = (
        budget.model_calls
        - budget.planner_calls
        - budget.reflection_calls
        - budget.synthesis_calls
    )
    assert executor_calls == 1


def test_charge_tool_call_counts() -> None:
    budget = _budget()
    budget.charge_tool_call()
    budget.charge_tool_call()
    assert budget.tool_calls == 2


# --- RunBudget: hard caps -------------------------------------------------- #


def test_model_call_cap_raises_on_overflow() -> None:
    budget = _budget(max_model_calls=2)
    budget.charge_model_call(BudgetCategory.EXECUTOR)
    budget.charge_model_call(BudgetCategory.EXECUTOR)
    with pytest.raises(BudgetExceeded) as exc:
        budget.charge_model_call(BudgetCategory.EXECUTOR)
    assert exc.value.budget == "model_calls"
    assert exc.value.limit == 2
    assert exc.value.used == 2
    # The blocked call did not increment the counter past the cap.
    assert budget.model_calls == 2


def test_tool_call_cap_raises_on_overflow() -> None:
    budget = _budget(max_tool_calls=1)
    budget.charge_tool_call()
    with pytest.raises(BudgetExceeded) as exc:
        budget.charge_tool_call()
    assert exc.value.budget == "tool_calls"
    assert budget.tool_calls == 1


# --- RunBudget: retries / replans ------------------------------------------ #


def test_retries_are_tracked_per_task() -> None:
    budget = _budget(max_retries=2)
    assert budget.retries_left("t0") == 2
    budget.charge_retry("t0")
    assert budget.retries_left("t0") == 1
    assert budget.retries_left("t1") == 2  # independent per task
    budget.charge_retry("t0")
    assert budget.retries_left("t0") == 0


def test_replans_are_tracked_per_run() -> None:
    budget = _budget(max_replans=1)
    assert budget.replans_left() == 1
    budget.charge_replan()
    assert budget.replans_left() == 0


# --- RunBudget: inspection (pure reads) ------------------------------------ #


def test_remaining_and_would_exceed() -> None:
    budget = _budget(max_model_calls=3, max_tool_calls=1)
    assert budget.remaining("model_calls") == 3
    assert budget.would_exceed("model_calls") is False
    budget.charge_model_call(BudgetCategory.EXECUTOR)
    assert budget.remaining("model_calls") == 2
    budget.charge_tool_call()
    assert budget.remaining("tool_calls") == 0
    assert budget.would_exceed("tool_calls") is True


def test_remaining_unknown_kind_raises() -> None:
    with pytest.raises(ValueError, match="Unknown budget kind"):
        _budget().remaining("bogus")


def test_snapshot_is_an_immutable_copy() -> None:
    budget = _budget()
    budget.charge_model_call(BudgetCategory.PLANNER)
    budget.charge_retry("t0")
    snap = budget.snapshot()
    assert snap.model_calls == 1
    assert snap.planner_calls == 1
    assert snap.retries_used == {"t0": 1}
    # Snapshot does not track later mutation.
    budget.charge_model_call(BudgetCategory.PLANNER)
    budget.charge_retry("t0")
    assert snap.model_calls == 1
    assert snap.retries_used == {"t0": 1}


def test_from_settings_reads_config() -> None:
    budget = RunBudget.from_settings(
        Settings(
            environment="test",
            agent_max_model_calls=11,
            agent_max_tool_calls=7,
            agent_max_retries=3,
            agent_max_replans=2,
        )
    )
    assert (budget.max_model_calls, budget.max_tool_calls) == (11, 7)
    assert (budget.max_retries, budget.max_replans) == (3, 2)


# --- BudgetedGateway ------------------------------------------------------- #


async def test_budgeted_gateway_charges_and_delegates() -> None:
    budget = _budget()
    gateway = BudgetedGateway(
        ScriptedGateway(["a", "b"]), budget, BudgetCategory.REFLECTION
    )
    assert await gateway.complete([]) == "a"
    assert await gateway.complete([]) == "b"
    assert budget.model_calls == 2
    assert budget.reflection_calls == 2


async def test_budgeted_gateway_stream_charges_once() -> None:
    budget = _budget()
    gateway = BudgetedGateway(
        ScriptedGateway(["hello"]), budget, BudgetCategory.EXECUTOR
    )
    chunks = [c async for c in gateway.stream([])]
    assert "".join(chunks) == "hello"
    assert budget.model_calls == 1


async def test_budgeted_gateway_raises_before_calling_inner() -> None:
    inner = ScriptedGateway([])  # any call would raise (exhausted)
    budget = _budget(max_model_calls=0)
    gateway = BudgetedGateway(inner, budget, BudgetCategory.PLANNER)
    with pytest.raises(BudgetExceeded):
        await gateway.complete([])
    assert inner.calls == []  # inner never invoked
    assert budget.planner_calls == 0


# --- BudgetedToolRegistry -------------------------------------------------- #


async def test_budgeted_registry_charges_and_delegates() -> None:
    budget = _budget()
    registry = BudgetedToolRegistry(_registry(), budget)
    result = await registry.execute("ping", {})
    assert result.ok and result.output == "pong"
    assert budget.tool_calls == 1


async def test_budgeted_registry_overflow_propagates_not_trapped() -> None:
    budget = _budget(max_tool_calls=1)
    inner = _registry()
    registry = BudgetedToolRegistry(inner, budget)
    await registry.execute("ping", {})
    # The blocked call raises BudgetExceeded — it is NOT turned into a failed
    # ToolResult observation.
    with pytest.raises(BudgetExceeded):
        await registry.execute("ping", {})
    assert budget.tool_calls == 1


def test_budgeted_registry_delegates_discovery() -> None:
    budget = _budget()
    registry = BudgetedToolRegistry(_registry(), budget)
    assert registry.names() == ["ping"]
    assert [s.name for s in registry.specs()] == ["ping"]
    assert registry.get("ping") is not None
    assert registry.get("missing") is None
