"""End-to-end M4 reflection loop through RunManager (Phase 5: retry loop core).

Reflection is opt-in (``agent_enable_reflection=True``). These cover retry
success, retry exhaustion (→ run FAILED for now), and reflection-disabled parity.
Deterministic via the scripted FakeLLM. Replan/abort escalation and cancellation
refinements arrive in later phases.
"""

from __future__ import annotations

import asyncio
import json

from atlas.agent.schemas import RunStatus, TaskStatus
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.llm.gateway import LLMError
from atlas.llm.providers.scripted import ScriptedGateway
from atlas.persistence.database import Database
from atlas.persistence.models import TaskRow
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskAttemptRepository,
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


def _finish(answer: str) -> str:
    return json.dumps({"thought": "done", "action": "finish", "answer": answer})


def _tool_call(tool: str, **arguments: object) -> str:
    return json.dumps(
        {
            "thought": "use tool",
            "action": "tool_call",
            "tool": tool,
            "arguments": arguments,
        }
    )


def _reflect(decision: str, reason: str = "", confidence: float = 0.8) -> str:
    return json.dumps(
        {"decision": decision, "reason": reason, "confidence": confidence}
    )


class _CancelAtCall(ScriptedGateway):
    """Sets the run's cancel event once ``after_call`` completions have happened."""

    def __init__(self, responses: list[str], *, after_call: int) -> None:
        super().__init__(responses)
        self.cancel: asyncio.Event | None = None
        self._after = after_call

    async def complete(self, messages, *, model=None, temperature=None) -> str:
        out = await super().complete(messages, model=model, temperature=temperature)
        if self.cancel is not None and len(self.calls) >= self._after:
            self.cancel.set()
        return out


def _manager(
    database: Database, gateway: ScriptedGateway, **overrides: object
) -> RunManager:
    emitter = EventEmitter(database, EventHub())
    settings = Settings(environment="test", **overrides)  # type: ignore[arg-type]
    return RunManager(database, emitter, gateway, _registry(), settings)


async def _events(database: Database, run_id: str) -> list:
    async with database.session() as session:
        return await EventRepository(session).list_after(run_id, 0)


_NON_TERMINAL = {TaskStatus.PENDING, TaskStatus.RUNNING, TaskStatus.RETRYING}


async def _run_cancelling(
    database: Database, gateway: _CancelAtCall, **overrides: object
) -> tuple[str, list]:
    """Start a run whose gateway self-cancels, wire the cancel event, and drain."""
    manager = _manager(database, gateway, **overrides)
    view = await manager.create_run("goal")
    gateway.cancel = manager._cancels[view.id]
    await manager.wait_for(view.id)
    return view.id, [e.type for e in await _events(database, view.id)]


async def _tasks(database: Database, run_id: str) -> list:
    async with database.session() as session:
        return await TaskRepository(session).list_for_run(run_id)


# --------------------------------------------------------------------------- #
# Retry: a poor first attempt is retried and then accepted
# --------------------------------------------------------------------------- #


async def test_retry_then_accept(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "Write the summary.", "suggested_tool": None}),
            _finish("bad summary"),  # attempt 1
            _reflect("retry", "missing the key figure"),  # → retry
            _finish("good summary"),  # attempt 2
            _reflect("accept", "looks good"),  # → accept
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("Summarize the report.")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
        attempts = await TaskAttemptRepository(session).list_for_task(tasks[0].id)
        task_row = await session.get(TaskRow, tasks[0].id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "good summary"  # single task → short-circuits synthesis
    assert tasks[0].status == TaskStatus.DONE
    assert tasks[0].output == "good summary"
    assert task_row is not None
    assert task_row.attempt_count == 2  # denormalized latest attempt

    # Both attempts persisted with their reflection verdicts.
    assert [a.attempt_number for a in attempts] == [1, 2]
    assert attempts[0].output == "bad summary"
    assert str(attempts[0].reflection_decision) == "retry"
    assert str(attempts[1].reflection_decision) == "accept"

    # Event trajectory: started → reflected(retry) → retrying → reflected → completed.
    assert types.count(EventType.TASK_REFLECTED) == 2
    assert types.count(EventType.TASK_RETRYING) == 1
    assert types.count(EventType.TASK_STARTED) == 1  # started once, not per attempt
    assert (
        types.index(EventType.TASK_RETRYING)
        < types.index(EventType.TASK_COMPLETED)
    )
    assert types[-1] == EventType.RUN_COMPLETED


async def test_retry_carries_critique_into_next_attempt(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "Do the thing."}),
            _finish("first try"),
            _reflect("retry", "you forgot the citation"),
            _finish("second try"),
            _reflect("accept"),
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    # The 2nd executor turn (4th call) saw the critique + prior output.
    second_attempt_prompt = gateway.calls[3][-1].content
    assert "you forgot the citation" in second_attempt_prompt
    assert "first try" in second_attempt_prompt


# --------------------------------------------------------------------------- #
# Retry exhaustion with no replan budget → graceful abort → FAILED (Phase 8).
# With no completed work there is nothing to synthesize, so no partial answer.
# --------------------------------------------------------------------------- #


async def test_retry_exhaustion_fails_run(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "impossible task"}),
            _finish("attempt 1"),
            _reflect("retry", "not good enough"),
            _finish("attempt 2"),
            _reflect("retry", "still not good"),
        ]
    )
    # max_retries=1 → 2 attempts; max_replans=0 → retry-exhaustion aborts (no replan).
    manager = _manager(
        database,
        gateway,
        agent_enable_reflection=True,
        agent_max_retries=1,
        agent_max_replans=0,
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
        attempts = await TaskAttemptRepository(session).list_for_task(tasks[0].id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert tasks[0].status == TaskStatus.FAILED
    assert len(attempts) == 2  # all attempts preserved
    assert types.count(EventType.TASK_RETRYING) == 1
    assert EventType.TASK_FAILED in types
    assert types[-1] == EventType.RUN_FAILED
    assert EventType.ANSWER_COMPLETED not in types  # no partial in Phase 5


async def test_executor_error_is_retried_when_reflection_enabled(
    database: Database,
) -> None:
    # Attempt 1 never finishes within its iteration budget → ExecutorError. With
    # reflection on this becomes a failed attempt (output "ERROR: …") that the
    # pre-check retries; attempt 2 finishes and is accepted.
    gateway = ScriptedGateway(
        [
            _plan({"description": "flaky task", "suggested_tool": "calculator"}),
            _tool_call("calculator", expression="1+1"),  # attempt 1: no finish → error
            _finish("recovered"),  # attempt 2: finishes
            _reflect("accept"),
        ]
    )
    manager = _manager(
        database,
        gateway,
        agent_enable_reflection=True,
        agent_max_iterations=1,
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
        attempts = await TaskAttemptRepository(session).list_for_task(tasks[0].id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert tasks[0].output == "recovered"
    assert len(attempts) == 2
    assert attempts[0].error is not None  # the failed first attempt is recorded
    assert types.count(EventType.TASK_RETRYING) == 1


async def test_empty_output_precheck_retries_without_reflection_call(
    database: Database,
) -> None:
    # Attempt 1 finishes with an empty answer → the reflector's deterministic
    # pre-check retries WITHOUT consulting the model (no reflect JSON scripted
    # for attempt 1); attempt 2 succeeds and is accepted.
    gateway = ScriptedGateway(
        [
            _plan({"description": "produce text"}),
            _finish(""),  # attempt 1 → empty → pre-check retry (no LLM reflect)
            _finish("real content"),  # attempt 2
            _reflect("accept"),
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert types.count(EventType.TASK_RETRYING) == 1
    assert gateway.remaining == 0  # exact: no extra reflect call was consumed


# --------------------------------------------------------------------------- #
# Reflection disabled → exact M3 behavior (no reflection calls/events)
# --------------------------------------------------------------------------- #


async def test_reflection_disabled_is_m3_behavior(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "Explain hash maps.", "suggested_tool": None}),
            _finish("A hash map maps keys to values."),
        ]
    )
    # Default settings have reflection disabled; no reflect responses scripted.
    manager = _manager(database, gateway)

    view = await manager.create_run("Explain hash maps.")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "A hash map maps keys to values."
    assert tasks[0].status == TaskStatus.DONE
    assert EventType.TASK_REFLECTED not in types
    assert EventType.TASK_RETRYING not in types
    assert gateway.remaining == 0  # planner + one executor turn only


# --------------------------------------------------------------------------- #
# Cancellation (Phase 6): every task ends terminal; no reflect/retry after cancel
# --------------------------------------------------------------------------- #


async def test_cancel_before_first_task_skips_all(database: Database) -> None:
    # Cancel lands right after planning → the task never starts and is SKIPPED.
    gateway = _CancelAtCall([_plan({"description": "t0"})], after_call=1)
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)

    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
    assert run.status == RunStatus.CANCELLED.value
    assert tasks[0].status == TaskStatus.SKIPPED
    assert EventType.TASK_STARTED not in types
    assert EventType.TASK_CANCELLED not in types
    assert EventType.TASK_SKIPPED in types
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_during_executor(database: Database) -> None:
    gateway = _CancelAtCall(
        [_plan({"description": "t0", "suggested_tool": "calculator"}),
         _tool_call("calculator", expression="1+1")],
        after_call=2,
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert all(t.status not in _NON_TERMINAL for t in tasks)  # no stale RUNNING
    assert EventType.TASK_COMPLETED not in types
    assert EventType.TASK_REFLECTED not in types  # cancelled before reflection
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_after_executor_before_reflection(database: Database) -> None:
    gateway = _CancelAtCall(
        [_plan({"description": "t0"}), _finish("produced output")], after_call=2
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)
    async with database.session() as session:
        attempts = await TaskAttemptRepository(session).list_for_task(tasks[0].id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert EventType.TASK_REFLECTED not in types  # never reflected after cancel
    assert len(attempts) == 1  # the finished attempt is persisted
    assert attempts[0].output == "produced output"
    assert gateway.remaining == 0  # planner + executor only; no reflection call
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_after_reflection_does_not_complete(database: Database) -> None:
    gateway = _CancelAtCall(
        [_plan({"description": "t0"}), _finish("out"), _reflect("accept")],
        after_call=3,
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert types.count(EventType.TASK_REFLECTED) == 1  # reflection ran and is logged
    assert EventType.TASK_COMPLETED not in types  # but the task is not completed
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_after_retry_verdict_does_not_schedule_retry(
    database: Database,
) -> None:
    gateway = _CancelAtCall(
        [_plan({"description": "t0"}), _finish("bad"), _reflect("retry", "fix it")],
        after_call=3,
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert EventType.TASK_RETRYING not in types  # no retry scheduled after cancel
    assert EventType.TASK_COMPLETED not in types
    assert types[-1] == EventType.RUN_CANCELLED


async def test_cancel_during_retry_attempt(database: Database) -> None:
    gateway = _CancelAtCall(
        [
            _plan({"description": "t0", "suggested_tool": "calculator"}),
            _finish("bad"),  # attempt 1
            _reflect("retry", "improve"),  # → schedules retry
            _tool_call("calculator", expression="1+1"),  # attempt 2 (cancel here)
        ],
        after_call=4,
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)
    async with database.session() as session:
        attempts = await TaskAttemptRepository(session).list_for_task(tasks[0].id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert all(t.status not in _NON_TERMINAL for t in tasks)  # no stale RETRYING
    assert types.count(EventType.TASK_RETRYING) == 1  # retry was scheduled first
    assert len(attempts) == 1  # attempt 2 raised before its row was written
    assert types[-1] == EventType.RUN_CANCELLED


async def test_executor_exception_with_cancellation_is_cancelled_not_failed(
    database: Database,
) -> None:
    gateway = _CancelAtCall(
        [_plan({"description": "t0", "suggested_tool": "calculator"}),
         _tool_call("calculator", expression="1+1")],
        after_call=2,
    )
    # max_iterations=1 → the tool_call attempt never finishes → ExecutorError, but
    # cancellation was signalled, so it resolves to CANCELLED (not FAILED).
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True, agent_max_iterations=1
    )
    tasks = await _tasks(database, run_id)

    assert tasks[0].status == TaskStatus.CANCELLED
    assert EventType.TASK_FAILED not in types
    assert types[-1] == EventType.RUN_CANCELLED


async def test_repeated_cancellation_is_idempotent(database: Database) -> None:
    gateway = ScriptedGateway([_plan({"description": "t0"})])
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    # Cancel is scheduled before the background task runs → observed in planning.
    assert await manager.cancel_run(view.id) is True
    assert await manager.cancel_run(view.id) is True  # second call: still active
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
    types = [e.type for e in await _events(database, view.id)]
    assert run.status == RunStatus.CANCELLED.value
    assert types.count(EventType.RUN_CANCELLED) == 1  # finalized exactly once
    # After completion the run is no longer cancellable.
    assert await manager.cancel_run(view.id) is False


async def test_cancellation_event_ordering(database: Database) -> None:
    # In-flight task CANCELLED, remaining SKIPPED: task.cancelled → task.skipped
    # → run.cancelled, with strictly increasing sequence numbers.
    gateway = _CancelAtCall(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("out"),  # task 0 executor; cancel set right after
        ],
        after_call=2,
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)
    view = await manager.create_run("goal")
    gateway.cancel = manager._cancels[view.id]
    await manager.wait_for(view.id)

    events = await _events(database, view.id)
    types = [e.type for e in events]
    tasks = await _tasks(database, view.id)

    assert tasks[0].status == TaskStatus.CANCELLED  # in-flight
    assert tasks[1].status == TaskStatus.SKIPPED  # never started
    assert all(t.status not in _NON_TERMINAL for t in tasks)
    assert (
        types.index(EventType.TASK_CANCELLED)
        < types.index(EventType.TASK_SKIPPED)
        < types.index(EventType.RUN_CANCELLED)
    )
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    # The cancelled task's event records the phase it was observed in.
    cancelled_evt = next(e for e in events if e.type == EventType.TASK_CANCELLED)
    assert cancelled_evt.payload["phase"] == "execution"


async def test_cancel_after_final_task_during_synthesis(database: Database) -> None:
    # Two tasks complete, then cancel lands during synthesis. Tasks stay DONE.
    gateway = _CancelAtCall(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("a"),
            _finish("b"),
            "the final answer",
        ],
        after_call=4,
    )
    run_id, types = await _run_cancelling(database, gateway)  # reflection disabled
    tasks = await _tasks(database, run_id)

    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
    assert run.status == RunStatus.CANCELLED.value
    assert [t.status for t in tasks] == [TaskStatus.DONE, TaskStatus.DONE]
    assert EventType.ANSWER_COMPLETED not in types
    assert types[-1] == EventType.RUN_CANCELLED


# --------------------------------------------------------------------------- #
# Replanning (Phase 7): a replan verdict swaps the remaining plan for a new
# generation; completed tasks and attempt history are preserved.
# --------------------------------------------------------------------------- #


class _FailAtReplan(ScriptedGateway):
    """Raises ``LLMError`` once ``fail_after`` completions have succeeded."""

    def __init__(self, responses: list[str], *, fail_after: int) -> None:
        super().__init__(responses)
        self._fail_after = fail_after

    async def complete(self, messages, *, model=None, temperature=None) -> str:
        if len(self.calls) >= self._fail_after:
            self.calls.append(list(messages))
            raise LLMError("planner provider unavailable")
        return await super().complete(messages, model=model, temperature=temperature)


async def test_replan_swaps_remaining_and_completes(database: Database) -> None:
    # t0 accepted; t1 → replan → a new generation (t2) runs and is accepted. The
    # completed task is preserved, the obsolete task skipped, generation increments.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),  # t0 executor
            _reflect("accept"),  # t0 → accept
            _finish("t1 attempt"),  # t1 executor
            _reflect("replan", "wrong approach for the rest"),  # t1 → replan
            _plan({"description": "t2"}),  # Planner.replan → new generation
            _finish("t2 done"),  # t2 executor
            _reflect("accept"),  # t2 → accept
            "the final synthesized answer",  # synthesis (2 outputs: t0, t2)
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
        t2_row = await session.get(TaskRow, f"{view.id}:2")
    events = await _events(database, view.id)
    types = [e.type for e in events]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "the final synthesized answer"

    # t0 preserved (untouched), t1 dropped by the replan, t2 the new generation.
    by_index = {t.index: t for t in tasks}
    assert by_index[0].status == TaskStatus.DONE
    assert by_index[0].output == "t0 done"
    assert by_index[1].status == TaskStatus.SKIPPED
    assert by_index[2].status == TaskStatus.DONE
    assert by_index[2].output == "t2 done"

    # Generation lineage recorded on the new task (Amendment 9).
    assert t2_row is not None
    assert t2_row.replan_generation == 1
    assert t2_row.parent_generation == 0

    # TaskView (API projection) exposes the M4 fields additively (Phase 9).
    assert by_index[0].attempt_count == 1
    assert by_index[0].replan_generation == 0
    assert by_index[0].parent_generation is None
    assert by_index[2].replan_generation == 1
    assert by_index[2].parent_generation == 0

    # plan.replanned carries the generation, dropped ids, counts, and reason.
    assert types.count(EventType.PLAN_REPLANNED) == 1
    replanned = next(e for e in events if e.type == EventType.PLAN_REPLANNED)
    assert replanned.payload["generation"] == 1
    assert replanned.payload["parent_generation"] == 0
    assert replanned.payload["dropped_task_ids"] == [f"{view.id}:1"]
    assert replanned.payload["new_task_count"] == 1
    assert replanned.payload["reason"] == "wrong approach for the rest"

    # Ordering: obsolete task skipped → plan.replanned → the new task starts.
    started = [i for i, t in enumerate(types) if t == EventType.TASK_STARTED]
    assert (
        types.index(EventType.TASK_SKIPPED)
        < types.index(EventType.PLAN_REPLANNED)
        < started[-1]  # t2's task.started
    )
    assert types[-1] == EventType.RUN_COMPLETED
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert gateway.remaining == 0  # exact call budget


async def test_replan_preserves_completed_attempt_history(database: Database) -> None:
    # A task retried-then-accepted before the replan keeps both its attempts.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 bad"),  # t0 attempt 1
            _reflect("retry", "fix it"),  # t0 → retry
            _finish("t0 good"),  # t0 attempt 2
            _reflect("accept"),  # t0 → accept
            _finish("t1 attempt"),  # t1 executor
            _reflect("replan", "rethink"),  # t1 → replan
            _plan({"description": "t2"}),  # Planner.replan
            _finish("t2 done"),  # t2 executor
            _reflect("accept"),  # t2 → accept
            "final answer",  # synthesis
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        t0_attempts = await TaskAttemptRepository(session).list_for_task(f"{view.id}:0")

    assert run.status == RunStatus.DONE.value
    # t0's full attempt history survives the later replan (I-1).
    assert [a.attempt_number for a in t0_attempts] == [1, 2]
    assert t0_attempts[0].output == "t0 bad"
    assert t0_attempts[1].output == "t0 good"


async def test_replan_budget_exhausted_fails_run(database: Database) -> None:
    # max_replans=1: the first replan runs; a second replan verdict escalates to a
    # run failure (Phase 8 adds a graceful partial answer).
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}),
            _finish("t0 out"),  # t0 executor
            _reflect("replan", "first replan"),  # t0 → replan #1 (allowed)
            _plan({"description": "t1"}),  # Planner.replan → t1
            _finish("t1 out"),  # t1 executor
            _reflect("replan", "second replan"),  # t1 → replan (no budget → fail)
        ]
    )
    manager = _manager(
        database, gateway, agent_enable_reflection=True, agent_max_replans=1
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert types.count(EventType.PLAN_REPLANNED) == 1  # exactly one replan happened
    by_index = {t.index: t for t in tasks}
    assert by_index[0].status == TaskStatus.SKIPPED  # dropped by the first replan
    assert by_index[1].status == TaskStatus.FAILED  # the escalated task
    assert EventType.ANSWER_COMPLETED not in types  # no partial answer in Phase 7
    assert types[-1] == EventType.RUN_FAILED


async def test_replan_invalid_plan_fails_run(database: Database) -> None:
    # The replan produces an unparseable plan → PlannerError → run FAILED with the
    # in-flight task failed (never left orphaned RUNNING).
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),
            _reflect("accept"),
            _finish("t1 attempt"),
            _reflect("replan", "rethink"),
            "this is not a valid plan",  # Planner.replan → unparseable
        ]
    )
    manager = _manager(
        database,
        gateway,
        agent_enable_reflection=True,
        agent_repair_attempts=0,  # no repair → immediate PlannerError
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert all(t.status not in _NON_TERMINAL for t in tasks)  # no orphaned RUNNING
    assert {t.index: t.status for t in tasks}[1] == TaskStatus.FAILED
    assert EventType.PLAN_REPLANNED not in types
    assert types[-1] == EventType.RUN_FAILED


async def test_replan_planner_llm_failure_fails_run(database: Database) -> None:
    # The Planner.replan model call itself fails → run FAILED, task failed.
    gateway = _FailAtReplan(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),
            _reflect("accept"),
            _finish("t1 attempt"),
            _reflect("replan", "rethink"),
        ],
        fail_after=5,  # the 6th call (Planner.replan) raises LLMError
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert all(t.status not in _NON_TERMINAL for t in tasks)
    assert EventType.PLAN_REPLANNED not in types
    assert types[-1] == EventType.RUN_FAILED


async def test_cancel_during_replanning(database: Database) -> None:
    # Cancel lands as Planner.replan returns → the after-replan checkpoint cancels:
    # the in-flight task → CANCELLED (phase "replan"), no new generation is created.
    gateway = _CancelAtCall(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),
            _reflect("accept"),
            _finish("t1 attempt"),
            _reflect("replan", "rethink"),
            _plan({"description": "t2"}),  # Planner.replan succeeds; cancel set after
        ],
        after_call=6,
    )
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True
    )
    tasks = await _tasks(database, run_id)

    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
    by_index = {t.index: t for t in tasks}

    assert run.status == RunStatus.CANCELLED.value
    assert by_index[0].status == TaskStatus.DONE  # completed work preserved
    assert by_index[1].status == TaskStatus.CANCELLED  # in-flight replan task
    assert all(t.status not in _NON_TERMINAL for t in tasks)
    assert len(tasks) == 2  # the new generation was never created
    assert EventType.PLAN_REPLANNED not in types
    cancelled_evt = next(
        e for e in await _events(database, run_id) if e.type == EventType.TASK_CANCELLED
    )
    assert cancelled_evt.payload["phase"] == "replan"
    assert types[-1] == EventType.RUN_CANCELLED


async def test_reflection_disabled_never_replans(database: Database) -> None:
    # Reflection off: a multi-task run completes exactly as M3 with no replanning.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("a"),
            _finish("b"),
            "final",  # synthesis
        ]
    )
    manager = _manager(database, gateway)  # reflection disabled (default)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert EventType.PLAN_REPLANNED not in types
    assert EventType.TASK_REFLECTED not in types
    assert gateway.remaining == 0


# --------------------------------------------------------------------------- #
# Escalation ladder (Phase 8): retry-exhaustion → replan → graceful abort.
# --------------------------------------------------------------------------- #


async def test_retry_exhaustion_escalates_to_replan(database: Database) -> None:
    # retries exhausted with a replan still available → replan (not abort).
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}),
            _finish("a1"),  # t0 attempt 1
            _reflect("retry", "r1"),  # → retry
            _finish("a2"),  # t0 attempt 2
            _reflect("retry", "r2"),  # retries exhausted → escalate → replan
            _plan({"description": "t1"}),  # Planner.replan
            _finish("t1 done"),  # t1
            _reflect("accept"),  # → accept
        ]
    )
    manager = _manager(
        database,
        gateway,
        agent_enable_reflection=True,
        agent_max_retries=1,
        agent_max_replans=1,
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "t1 done"  # single completed task → short-circuit
    assert types.count(EventType.PLAN_REPLANNED) == 1  # retry-exhaustion replanned
    assert types.count(EventType.TASK_RETRYING) == 1
    by_index = {t.index: t for t in tasks}
    assert by_index[0].status == TaskStatus.SKIPPED  # the exhausted task, dropped
    assert by_index[1].status == TaskStatus.DONE
    assert types[-1] == EventType.RUN_COMPLETED


async def test_abort_verdict_synthesizes_partial(database: Database) -> None:
    # An abort verdict preserves completed work as a marked partial answer, FAILED.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),  # t0 executor
            _reflect("accept"),  # t0 → accept
            _finish("t1 attempt"),  # t1 executor
            _reflect("abort", "cannot proceed with available tools"),  # t1 → abort
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert run.answer is not None
    assert "t0 done" in run.answer  # completed work preserved
    assert "partial" in run.answer.lower()  # clearly marked incomplete
    by_index = {t.index: t for t in tasks}
    assert by_index[0].status == TaskStatus.DONE
    assert by_index[1].status == TaskStatus.FAILED
    assert all(t.status not in _NON_TERMINAL for t in tasks)
    assert EventType.ANSWER_COMPLETED in types  # partial answer emitted
    assert types[-1] == EventType.RUN_FAILED
    assert types.count(EventType.RUN_FAILED) == 1  # no duplicate terminal event
    assert EventType.RUN_COMPLETED not in types

    # RunView (API projection) flags a FAILED-with-answer run as partial (ADR-0014).
    async with database.session() as session:
        run_row = await RunRepository(session).get(view.id)
    assert RunRepository.to_view(run_row).partial is True


async def test_partial_contains_completed_work_only(database: Database) -> None:
    # Two tasks complete, the third aborts → synthesis sees only completed outputs,
    # never the aborted task's content or skipped work.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}, {"description": "t2"}),
            _finish("alpha result"),  # t0
            _reflect("accept"),
            _finish("beta result"),  # t1
            _reflect("accept"),
            _finish("gamma aborted content"),  # t2 (its output is discarded)
            _reflect("abort", "stop here"),  # t2 → abort
            "SYNTHESIZED PARTIAL",  # synthesis over t0+t1 (2 outputs → real call)
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)

    assert run.status == RunStatus.FAILED.value
    assert "SYNTHESIZED PARTIAL" in run.answer
    # The synthesis prompt (7th completion) was grounded ONLY in completed outputs.
    synthesis_prompt = gateway.calls[7][-1].content
    assert "alpha result" in synthesis_prompt
    assert "beta result" in synthesis_prompt
    assert "gamma aborted content" not in synthesis_prompt


# --------------------------------------------------------------------------- #
# Budget enforcement (Phase 8): hard model/tool caps → FAILED, no partial.
# --------------------------------------------------------------------------- #


async def test_model_budget_exceeded_fails_run(database: Database) -> None:
    # max_model_calls=1: the planner call fits; the first executor call overflows.
    gateway = ScriptedGateway([_plan({"description": "t0"})])
    manager = _manager(
        database, gateway, agent_enable_reflection=True, agent_max_model_calls=1
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    events = await _events(database, view.id)
    types = [e.type for e in events]

    assert run.status == RunStatus.FAILED.value
    assert tasks[0].status == TaskStatus.FAILED
    assert all(t.status not in _NON_TERMINAL for t in tasks)  # no stale RUNNING
    assert EventType.ANSWER_COMPLETED not in types  # budget hit → no partial
    assert run.answer is None
    # Ordering: budget.exceeded → task.failed → run.failed; exactly one terminal.
    budget_evt = next(e for e in events if e.type == EventType.BUDGET_EXCEEDED)
    assert budget_evt.payload["budget"] == "model_calls"
    assert budget_evt.payload["limit"] == 1
    assert budget_evt.payload["task_id"] == tasks[0].id
    assert (
        types.index(EventType.BUDGET_EXCEEDED)
        < types.index(EventType.TASK_FAILED)
        < types.index(EventType.RUN_FAILED)
    )
    assert types.count(EventType.RUN_FAILED) == 1
    assert EventType.RUN_COMPLETED not in types
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)


async def test_tool_budget_exceeded_fails_run(database: Database) -> None:
    # max_tool_calls=1: the first tool call fits; the second overflows and is NOT
    # trapped as an observation (it propagates out of the executor).
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0", "suggested_tool": "calculator"}),
            _tool_call("calculator", expression="1+1"),  # tool call 1 (ok)
            _tool_call("calculator", expression="2+2"),  # tool call 2 (overflow)
        ]
    )
    manager = _manager(
        database, gateway, agent_enable_reflection=True, agent_max_tool_calls=1
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
        tasks = await TaskRepository(session).list_for_run(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.FAILED.value
    assert tasks[0].status == TaskStatus.FAILED
    budget_evt = next(
        e for e in await _events(database, view.id) if e.type == EventType.BUDGET_EXCEEDED
    )
    assert budget_evt.payload["budget"] == "tool_calls"
    assert EventType.ANSWER_COMPLETED not in types
    assert types[-1] == EventType.RUN_FAILED


async def test_cancellation_takes_precedence_over_budget(database: Database) -> None:
    # A tiny model budget would fail the run, but a cancellation landing first wins:
    # the run is CANCELLED with no budget.exceeded / run.failed.
    gateway = _CancelAtCall([_plan({"description": "t0"})], after_call=1)
    run_id, types = await _run_cancelling(
        database, gateway, agent_enable_reflection=True, agent_max_model_calls=1
    )
    tasks = await _tasks(database, run_id)

    async with database.session() as session:
        run = await RunRepository(session).get(run_id)
    assert run.status == RunStatus.CANCELLED.value
    assert tasks[0].status == TaskStatus.SKIPPED
    assert EventType.BUDGET_EXCEEDED not in types
    assert EventType.RUN_FAILED not in types
    assert types[-1] == EventType.RUN_CANCELLED


async def test_reflection_disabled_ignores_generous_budget(database: Database) -> None:
    # Reflection off + default budgets: a normal multi-task run is exactly M3 —
    # no budget/abort/reflection machinery is observable.
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("a"),
            _finish("b"),
            "final",  # synthesis
        ]
    )
    manager = _manager(database, gateway)  # reflection disabled (default budgets)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    async with database.session() as session:
        run = await RunRepository(session).get(view.id)
    types = [e.type for e in await _events(database, view.id)]

    assert run.status == RunStatus.DONE.value
    assert run.answer == "final"
    assert EventType.BUDGET_EXCEEDED not in types
    assert EventType.TASK_FAILED not in types
    assert EventType.TASK_REFLECTED not in types
    assert gateway.remaining == 0
    assert RunRepository.to_view(run).partial is False  # DONE run is not partial


# --------------------------------------------------------------------------- #
# Executor event attribution (Phase 9): thought/tool.* carry attempt + generation.
# --------------------------------------------------------------------------- #


async def test_executor_events_carry_attempt_and_generation(database: Database) -> None:
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}),
            _finish("bad"),  # t0 attempt 1 (generation 0)
            _reflect("retry", "again"),  # → retry
            _finish("good"),  # t0 attempt 2 (generation 0)
            _reflect("replan", "rethink the rest"),  # → replan
            _plan({"description": "t1"}),  # replan → generation 1
            _finish("t1 out"),  # t1 attempt 1 (generation 1)
            _reflect("accept"),
        ]
    )
    manager = _manager(
        database,
        gateway,
        agent_enable_reflection=True,
        agent_max_retries=1,
        agent_max_replans=1,
    )

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    events = await _events(database, view.id)
    thoughts = [e for e in events if e.type == EventType.THOUGHT]
    t0 = [e for e in thoughts if e.payload["task_id"] == f"{view.id}:0"]
    t1 = [e for e in thoughts if e.payload["task_id"] == f"{view.id}:1"]

    # t0 emitted a thought per attempt, each stamped with attempt# and generation 0.
    assert [e.payload["attempt"] for e in t0] == [1, 2]
    assert all(e.payload["generation"] == 0 for e in t0)
    # t1 is the replanned generation: its events carry generation 1, attempt 1.
    assert t1 and all(e.payload["generation"] == 1 for e in t1)
    assert all(e.payload["attempt"] == 1 for e in t1)


def _replay_task_states(events: list) -> dict[str, str]:
    """Reconstruct each task's final status from the event ledger alone (I-1).

    Mirrors the frontend reducer (`useRunStream`): plan.created/plan.replanned seed
    tasks; task.* transitions them. Proves the ledger is the source of truth.
    """
    status: dict[str, str] = {}
    transition = {
        EventType.TASK_STARTED: "running",
        EventType.TASK_RETRYING: "retrying",
        EventType.TASK_COMPLETED: "done",
        EventType.TASK_FAILED: "failed",
        EventType.TASK_SKIPPED: "skipped",
        EventType.TASK_CANCELLED: "cancelled",
    }
    for e in events:
        if e.type in (EventType.PLAN_CREATED, EventType.PLAN_REPLANNED):
            for t in e.payload["tasks"]:
                status[str(t["id"])] = "pending"
            for dropped in e.payload.get("dropped_task_ids", []):
                status[str(dropped)] = "skipped"
        elif e.type in transition:
            status[str(e.payload["task_id"])] = transition[e.type]
    return status


async def test_event_replay_reconstructs_task_states(database: Database) -> None:
    # A retry+replan run: reconstructing task states from events only must equal the
    # persisted `tasks` projection (checklist ↔ ledger parity, invariant I-1).
    gateway = ScriptedGateway(
        [
            _plan({"description": "t0"}, {"description": "t1"}),
            _finish("t0 done"),
            _reflect("accept"),
            _finish("t1 attempt"),
            _reflect("replan", "rethink"),
            _plan({"description": "t2"}),
            _finish("t2 done"),
            _reflect("accept"),
            "final",
        ]
    )
    manager = _manager(database, gateway, agent_enable_reflection=True)

    view = await manager.create_run("goal")
    await manager.wait_for(view.id)

    events = await _events(database, view.id)
    tasks = await _tasks(database, view.id)

    replayed = _replay_task_states(events)
    persisted = {t.id: t.status.value for t in tasks}
    assert replayed == persisted  # ledger fully reconstructs the projection
