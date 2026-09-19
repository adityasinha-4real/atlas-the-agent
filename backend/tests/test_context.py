"""Context Builder: task framing, prior-output inclusion, and truncation."""

from __future__ import annotations

from datetime import UTC, datetime

from atlas.agent.context import ContextBuilder
from atlas.agent.schemas import PriorTaskOutput, TaskContext, TaskStatus, TaskView
from atlas.core.config import Settings
from atlas.tools.calculator import CalculatorTool
from atlas.tools.registry import ToolRegistry


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(CalculatorTool())
    return registry


def _task(index: int, description: str, **kwargs) -> TaskView:
    now = datetime.now(UTC)
    return TaskView(
        id=f"run:{index}",
        run_id="run",
        index=index,
        description=description,
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
        **kwargs,
    )


def test_includes_description_success_criteria_and_tools() -> None:
    builder = ContextBuilder(_registry(), Settings(environment="test"))
    ctx = TaskContext(
        goal="do the thing",
        task=_task(
            0,
            "compute a sum",
            success_criteria="the sum is known",
            suggested_tool="calculator",
        ),
        total_tasks=1,
    )

    messages = builder.build(ctx)

    assert messages[0].role == "system"
    assert "calculator" in messages[0].content  # tool listed in system prompt
    user = messages[1].content
    assert "do the thing" in user
    assert "compute a sum" in user
    assert "the sum is known" in user
    assert "calculator" in user


def test_includes_prior_outputs_and_position() -> None:
    builder = ContextBuilder(_registry(), Settings(environment="test"))
    ctx = TaskContext(
        goal="two step goal",
        task=_task(1, "second task"),
        prior_outputs=[
            PriorTaskOutput(index=0, description="first task", output="42")
        ],
        total_tasks=2,
    )

    user = builder.build(ctx)[1].content

    assert "task 2 of 2" in user
    assert "first task" in user
    assert "42" in user


def test_truncates_long_prior_outputs() -> None:
    builder = ContextBuilder(
        _registry(),
        Settings(environment="test", context_prior_output_chars=100),
    )
    ctx = TaskContext(
        goal="g",
        task=_task(1, "next"),
        prior_outputs=[
            PriorTaskOutput(index=0, description="big", output="x" * 5000)
        ],
        total_tasks=2,
    )

    user = builder.build(ctx)[1].content

    assert "…" in user
    assert "x" * 5000 not in user
    # The single prior line is bounded to roughly the budget, not 5000 chars.
    assert user.count("x") <= 100


def test_single_task_omits_plan_position() -> None:
    builder = ContextBuilder(_registry(), Settings(environment="test"))
    ctx = TaskContext(goal="g", task=_task(0, "only task"), total_tasks=1)

    user = builder.build(ctx)[1].content

    assert "task 1 of" not in user
    assert "Results from previous tasks" not in user


def test_retry_injects_critique_of_previous_attempt() -> None:
    builder = ContextBuilder(_registry(), Settings(environment="test"))
    ctx = TaskContext(
        goal="g",
        task=_task(0, "do it"),
        total_tasks=1,
        attempt=2,
        previous_output="my flawed first result",
        critique_reason="you missed the deadline field",
    )

    user = builder.build(ctx)[1].content

    assert "my flawed first result" in user
    assert "you missed the deadline field" in user
    assert "attempt at this task was judged insufficient" in user


def test_first_attempt_has_no_critique() -> None:
    builder = ContextBuilder(_registry(), Settings(environment="test"))
    ctx = TaskContext(
        goal="g",
        task=_task(0, "do it"),
        total_tasks=1,
        attempt=1,
        critique_reason="ignored on attempt 1",
    )

    user = builder.build(ctx)[1].content

    assert "insufficient" not in user
    assert "ignored on attempt 1" not in user
