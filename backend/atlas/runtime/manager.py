"""``RunManager`` — creates runs and drives their execution as background tasks.

Design notes:
* Runs are persisted ledgers; the in-memory task/cancel state is derived and
  disposable, so a future worker-queue runtime is a bounded refactor (§7).
* From M3 a run is: PLANNING (Planner → ordered task list) → RUNNING (each task
  through the reused M2 ``Executor``, its context assembled by the Context
  Builder) → SYNTHESIS (Synthesizer composes the final answer). The manager owns
  run/task lifecycle and answer delivery; the executor owns reasoning/tool events.
* Cancellation is cooperative via a per-run ``asyncio.Event`` checked during
  planning, between tasks, between executor iterations, and between answer chunks.
* Every error is turned into a ``run.failed`` event; nothing escapes the run
  boundary as an unhandled exception (design doc §1.2 principle). With reflection
  enabled (M4) a task is retried on a ``retry`` verdict within its budget; a
  ``replan`` verdict (or retry-exhaustion) swaps the remaining plan for a new
  generation; an ``abort`` verdict or exhausted retries+replans gracefully aborts,
  synthesizing a partial answer over completed work and finalizing FAILED. A single
  per-run ``RunBudget`` wraps the gateway and tool registry; a ``BudgetExceeded``
  finalizes FAILED with no partial answer (RFC-0002 §8, I-12).
"""

from __future__ import annotations

import asyncio
import logging
import uuid

from atlas.agent.context import ContextBuilder
from atlas.agent.executor import Executor, ExecutorError, RunCancelled
from atlas.agent.planner import Planner, PlannerError
from atlas.agent.prompts import build_partial_answer
from atlas.agent.reflector import Reflector
from atlas.agent.schemas import (
    PriorTaskOutput,
    ReflectionDecision,
    ReflectionResult,
    RunStatus,
    RunView,
    TaskContext,
    TaskStatus,
    TaskView,
)
from atlas.agent.synthesizer import Synthesizer
from atlas.core.config import Settings
from atlas.core.logging import bind_run_id, reset_run_id
from atlas.events.emit import EventEmitter
from atlas.events.types import EventType
from atlas.llm.gateway import LLMError, LLMGateway
from atlas.memory.schemas import MemoryOutcome
from atlas.memory.service import MemoryService
from atlas.persistence.database import Database
from atlas.persistence.repositories import (
    RunRepository,
    TaskAttemptRepository,
    TaskRepository,
)
from atlas.runtime.budget import (
    BudgetCategory,
    BudgetedGateway,
    BudgetedToolRegistry,
    BudgetExceeded,
    RunBudget,
)
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class _Cancelled(Exception):
    """Internal signal: cancellation was observed while a task was in flight.

    Carries the in-flight task (→ ``CANCELLED``) and the phase it was observed in
    so ``_run_tasks`` can finalize the run consistently (RFC-0002 §10). Never
    escapes the runtime.
    """

    def __init__(self, task: TaskView, phase: str) -> None:
        self.task = task
        self.phase = phase
        super().__init__(f"cancelled during {phase}")


class _Replan(Exception):
    """Internal signal: a task's verdict requires replanning (RFC-0002 §4).

    Carries the triggering task (dropped with the remaining plan) and its
    reflection verdict (its ``reason`` seeds the replan prompt). Caught by
    ``_run_tasks``, which swaps in the new generation and continues. Never escapes
    the runtime.
    """

    def __init__(self, task: TaskView, result: ReflectionResult | None) -> None:
        self.task = task
        self.result = result
        super().__init__("replan requested")


class RunManager:
    """Owns run creation, execution tasks, and cancellation."""

    def __init__(
        self,
        db: Database,
        emitter: EventEmitter,
        gateway: LLMGateway,
        registry: ToolRegistry,
        settings: Settings,
    ) -> None:
        self._db = db
        self._emitter = emitter
        self._gateway = gateway
        self._registry = registry
        self._settings = settings
        self._reflection_enabled = settings.agent_enable_reflection
        # Episodic memory (M5) is opt-in; with it off there is no memory object and
        # behavior is byte-for-byte M4 (invariant I-15).
        self._memory: MemoryService | None = (
            MemoryService(db, gateway, emitter, settings)
            if settings.memory_enabled
            else None
        )
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._cancels: dict[str, asyncio.Event] = {}
        # Lessons recalled once at plan time, reused by any replan (RFC-0003 §12).
        self._lessons: dict[str, str] = {}

    # -- Public API ---------------------------------------------------------- #

    async def create_run(self, goal: str) -> RunView:
        """Persist a new run (CREATED), emit ``run.created``, and schedule it."""
        run_id = uuid.uuid4().hex
        async with self._db.session() as session:
            repo = RunRepository(session)
            row = await repo.create(run_id, goal)
            view = RunRepository.to_view(row)
        await self._emitter.emit(run_id, EventType.RUN_CREATED, {"goal": goal})

        cancel = asyncio.Event()
        self._cancels[run_id] = cancel
        task = asyncio.create_task(self._execute(run_id, goal, cancel))
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._cleanup(rid))
        return view

    async def cancel_run(self, run_id: str) -> bool:
        """Request cooperative cancellation. Returns True if the run was active."""
        cancel = self._cancels.get(run_id)
        if cancel is None:
            return False
        cancel.set()
        return True

    async def wait_for(self, run_id: str) -> None:
        """Await a run's background task (used by tests and graceful shutdown)."""
        task = self._tasks.get(run_id)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self) -> None:
        """Cancel-signal all runs and await their tasks."""
        for cancel in self._cancels.values():
            cancel.set()
        tasks = list(self._tasks.values())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    # -- Execution ----------------------------------------------------------- #

    async def _execute(self, run_id: str, goal: str, cancel: asyncio.Event) -> None:
        """Plan the goal, execute its tasks in order, then synthesize the answer.

        A single ``RunBudget`` bounds the whole run; the gateway and tool registry
        are wrapped per component so every model/tool call is charged centrally
        (RFC-0002 §8). A ``BudgetExceeded`` from any phase finalizes the run
        ``FAILED`` with no partial answer (Amendment 4 / I-12).
        """
        budget = RunBudget.from_settings(self._settings)
        # Bind the run id for log correlation (M6, RFC-0004 §30). Purely a logging
        # concern — it changes no event, output, or ordering (invariant I-23).
        token = bind_run_id(run_id)
        try:
            tasks = await self._plan(run_id, goal, cancel, budget)
            if tasks is None:
                return  # cancelled during planning

            outputs = await self._run_tasks(run_id, goal, tasks, cancel, budget)
            if outputs is None:
                return  # cancelled, failed, or budget-exhausted (already finalized)

            await self._synthesize(run_id, goal, outputs, cancel, budget)

        except RunCancelled:
            await self._finalize_cancelled(run_id)
        except BudgetExceeded as exc:
            # A budget hit in planning or synthesis (no single in-flight task).
            await self._finalize_budget_exceeded(run_id, exc, task=None, cancel=cancel)
        except PlannerError as exc:
            logger.warning("Run %s failed (planner): %s", run_id, exc)
            await self._finalize_failed(run_id, f"Planning failed: {exc}")
        except LLMError as exc:
            logger.warning("Run %s failed (LLM): %s", run_id, exc)
            await self._finalize_failed(run_id, f"LLM error: {exc}")
        except ExecutorError as exc:
            logger.warning("Run %s failed (executor): %s", run_id, exc)
            await self._finalize_failed(run_id, str(exc))
        except Exception as exc:  # defensive: never leak from a run
            logger.exception("Run %s failed (unexpected)", run_id)
            await self._finalize_failed(run_id, f"Unexpected error: {exc}")
        finally:
            reset_run_id(token)

    async def _plan(
        self, run_id: str, goal: str, cancel: asyncio.Event, budget: RunBudget
    ) -> list[TaskView] | None:
        """PLANNING: produce and persist the task list. None if cancelled."""
        await self._set_status(run_id, RunStatus.PLANNING)
        await self._emitter.emit(run_id, EventType.RUN_STARTED, {})
        if cancel.is_set():
            await self._finalize_cancelled(run_id)  # no tasks persisted yet
            return None

        # Recall lessons from past runs once, before planning (RFC-0003 §7). Memory
        # is best-effort: a failure yields no lessons and plans exactly as M4 would.
        lessons: str | None = None
        if self._memory is not None:
            recalled = await self._memory.recall(run_id, goal, budget)
            self._lessons[run_id] = recalled.rendered
            lessons = recalled.rendered or None

        planner = Planner(
            self._budgeted_gateway(budget, BudgetCategory.PLANNER),
            self._registry,
            self._settings,
        )
        planned = await planner.plan(goal, lessons=lessons)

        async with self._db.session() as session:
            tasks = await TaskRepository(session).bulk_create(run_id, planned)
        await self._emitter.emit(
            run_id, EventType.PLAN_CREATED, {"tasks": [_task_brief(t) for t in tasks]}
        )
        if cancel.is_set():
            # Tasks exist now — skip them so none is left PENDING on the run.
            await self._finalize_cancelled_run(run_id, cancelled=None)
            return None
        return tasks

    async def _run_tasks(
        self,
        run_id: str,
        goal: str,
        tasks: list[TaskView],
        cancel: asyncio.Event,
        budget: RunBudget,
    ) -> list[PriorTaskOutput] | None:
        """RUNNING: execute each task in order. None if cancelled/failed/exhausted.

        With reflection disabled (default) each task runs exactly once — byte-for
        M3 behavior. With it enabled, each task runs through a reflect/retry loop
        (RFC-0002 §4); a ``replan`` verdict swaps the remaining plan for a new
        generation and continues (Phase 7); an ``abort``/exhaustion synthesizes a
        partial answer (Phase 8). The gateway and tool registry are budget-wrapped
        so a ``BudgetExceeded`` finalizes the run FAILED with no partial answer.
        """
        await self._set_status(run_id, RunStatus.RUNNING)
        builder = ContextBuilder(self._registry, self._settings)
        executor = Executor(
            self._budgeted_gateway(budget, BudgetCategory.EXECUTOR),
            BudgetedToolRegistry(self._registry, budget),
            self._emitter,
            self._settings,
        )
        reflector = Reflector(
            self._budgeted_gateway(budget, BudgetCategory.REFLECTION), self._settings
        )
        outputs: list[PriorTaskOutput] = []
        pending = list(tasks)

        # Each iteration runs one plan generation; a replan replaces ``pending``
        # with the new generation's tasks (completed ``outputs`` carry over).
        while pending:
            total = len(outputs) + len(pending)
            replanned = False
            for position, task in enumerate(pending):
                if cancel.is_set():
                    # Cancelled before this task started: it and the rest are unstarted.
                    await self._finalize_cancelled_run(run_id, cancelled=None)
                    return None
                try:
                    result = await self._run_task(
                        run_id,
                        goal,
                        task,
                        total,
                        outputs,
                        cancel,
                        builder,
                        executor,
                        reflector,
                        budget,
                    )
                except _Cancelled as c:
                    await self._finalize_cancelled_run(run_id, cancelled=c)
                    return None
                except BudgetExceeded as be:
                    # A hard model/tool cap tripped while this task was in flight.
                    await self._finalize_budget_exceeded(
                        run_id, be, task=task, cancel=cancel
                    )
                    return None
                except _Replan as r:
                    # Drop this task and everything after it; run the new plan.
                    new_pending = await self._do_replan(
                        run_id, goal, r, pending[position:], outputs, budget, cancel
                    )
                    if new_pending is None:
                        return None  # replan failed/cancelled/exhausted — finalized
                    pending = new_pending
                    replanned = True
                    break
                if result is None:
                    return None  # a task failed/aborted — already finalized
                outputs.append(result)
            if not replanned:
                return outputs
        return outputs

    async def _run_task(
        self,
        run_id: str,
        goal: str,
        task: TaskView,
        total_tasks: int,
        prior_outputs: list[PriorTaskOutput],
        cancel: asyncio.Event,
        builder: ContextBuilder,
        executor: Executor,
        reflector: Reflector,
        budget: RunBudget,
    ) -> PriorTaskOutput | None:
        """Run one task to acceptance via the attempt loop.

        Returns its output on ``accept``; returns ``None`` if it failed the run;
        raises ``_Cancelled`` (never treated as retry/failure) if cancellation is
        observed while the task is in flight. Cancellation is checked before each
        attempt, after execution, before/after reflection, and before a retry —
        and always overrides reflection and retry (RFC-0002 §10).
        """
        attempt = 1
        previous_output: str | None = None
        critique: str | None = None
        previous_reasons: list[str] = []

        while True:
            if cancel.is_set():  # before each (re)attempt
                raise _Cancelled(task, "execution")
            await self._mark_task_running(run_id, task.id)
            if attempt == 1:
                await self._emit_task_started(run_id, task)

            context = TaskContext(
                goal=goal,
                task=task,
                prior_outputs=list(prior_outputs),
                total_tasks=total_tasks,
                attempt=attempt,
                previous_output=previous_output,
                critique_reason=critique,
            )
            messages = builder.build(context)

            try:
                output = await executor.run(
                    run_id,
                    messages,
                    cancel,
                    task_id=task.id,
                    attempt=attempt,
                    generation=task.replan_generation,
                )
                exec_error: str | None = None
            except RunCancelled as exc:
                raise _Cancelled(task, "execution") from exc
            except (ExecutorError, LLMError) as exc:
                # Cancellation always overrides an execution failure.
                if cancel.is_set():
                    raise _Cancelled(task, "execution") from exc
                if not self._reflection_enabled:
                    # M3 behavior: an executor error fails the whole run.
                    await self._fail_task(run_id, task, str(exc))
                    await self._finalize_failed(
                        run_id, f"Task {task.index + 1} failed: {exc}"
                    )
                    return None
                # With reflection on, a failed execution is a failed attempt the
                # reflector can retry (its pre-check treats ``ERROR:`` as retry).
                output = f"ERROR: {exc}"
                exec_error = str(exc)

            # Persist the attempt, then honor cancellation before reflecting.
            attempt_id = await self._record_attempt(
                run_id, task, attempt, output, exec_error
            )
            if cancel.is_set():  # after executor / before reflection
                raise _Cancelled(task, "execution")

            if self._reflection_enabled:
                result: ReflectionResult | None = await reflector.reflect(
                    goal=goal,
                    task=task,
                    output=output,
                    attempt=attempt,
                    previous_reasons=previous_reasons,
                )
                await self._record_reflection(attempt_id, result)
                await self._emit_task_reflected(run_id, task, attempt, result)
                if cancel.is_set():  # after reflection — do not act on the verdict
                    raise _Cancelled(task, "reflection")
                decision = result.decision
            else:
                result = None
                decision = ReflectionDecision.ACCEPT

            if decision is ReflectionDecision.ACCEPT:
                await self._complete_task(run_id, task.id, output)
                await self._emit_task_completed(run_id, task, attempt, output)
                return PriorTaskOutput(
                    index=task.index, description=task.description, output=output
                )

            if (
                decision is ReflectionDecision.RETRY
                and result is not None
                and budget.retries_left(task.id) > 0
            ):
                if cancel.is_set():  # before scheduling a retry
                    raise _Cancelled(task, "execution")
                budget.charge_retry(task.id)
                previous_output = output
                critique = result.reason
                previous_reasons.append(result.reason)
                attempt += 1
                await self._mark_task_retrying(run_id, task.id)
                await self._emit_task_retrying(run_id, task, attempt, result.reason)
                continue

            # Retry exhausted, or a replan/abort verdict: escalate the ladder
            # (RFC-0002 §4) — retry-exhaustion/replan → replan if one remains, else
            # an abort synthesizes a partial answer over completed work.
            await self._escalate(
                run_id, goal, task, result, prior_outputs, budget, cancel
            )
            return None

    async def _synthesize(
        self,
        run_id: str,
        goal: str,
        outputs: list[PriorTaskOutput],
        cancel: asyncio.Event,
        budget: RunBudget,
    ) -> None:
        """SYNTHESIS: compose the final answer and finalize the run DONE."""
        if cancel.is_set():
            await self._finalize_cancelled(run_id)
            return
        synthesizer = Synthesizer(
            self._budgeted_gateway(budget, BudgetCategory.SYNTHESIS), self._settings
        )
        answer = await synthesizer.answer(goal, outputs)
        if await self._stream_answer(run_id, answer, cancel):
            await self._finalize_cancelled(run_id)
            return
        await self._finalize_done(run_id, answer)
        # After the answer is delivered and the run is DONE, distill a memory
        # (best-effort, off the perceived-latency path — RFC-0003 §6/§9).
        await self._write_memory(run_id, goal, MemoryOutcome.DONE, answer, budget)

    async def _stream_answer(
        self, run_id: str, answer: str, cancel: asyncio.Event
    ) -> bool:
        """Emit the answer as ``answer.token`` chunks. Returns True if cancelled."""
        for index, word in enumerate(answer.split(" ")):
            if cancel.is_set():
                return True
            chunk = word if index == 0 else f" {word}"
            await self._emitter.emit(run_id, EventType.ANSWER_TOKEN, {"text": chunk})
        return False

    # -- Finalizers ---------------------------------------------------------- #

    async def _finalize_done(self, run_id: str, answer: str) -> None:
        async with self._db.session() as session:
            repo = RunRepository(session)
            await repo.set_answer(run_id, answer)
            await repo.set_status(run_id, RunStatus.DONE)
        await self._emitter.emit(run_id, EventType.ANSWER_COMPLETED, {"text": answer})
        await self._emitter.emit(run_id, EventType.RUN_COMPLETED, {})

    async def _finalize_failed(self, run_id: str, error: str) -> None:
        async with self._db.session() as session:
            repo = RunRepository(session)
            await repo.set_error(run_id, error)
            await repo.set_status(run_id, RunStatus.FAILED)
        await self._emitter.emit(run_id, EventType.RUN_FAILED, {"error": error})

    async def _finalize_cancelled(self, run_id: str) -> None:
        await self._set_status(run_id, RunStatus.CANCELLED)
        await self._emitter.emit(run_id, EventType.RUN_CANCELLED, {})

    async def _set_status(self, run_id: str, status: RunStatus) -> None:
        async with self._db.session() as session:
            await RunRepository(session).set_status(run_id, status)

    # -- Task events (M4) ---------------------------------------------------- #

    async def _emit_task_started(self, run_id: str, task: TaskView) -> None:
        await self._emitter.emit(
            run_id,
            EventType.TASK_STARTED,
            {
                "task_id": task.id,
                "index": task.index,
                "description": task.description,
                "attempt": 1,
            },
        )

    async def _emit_task_completed(
        self, run_id: str, task: TaskView, attempt: int, output: str
    ) -> None:
        await self._emitter.emit(
            run_id,
            EventType.TASK_COMPLETED,
            {
                "task_id": task.id,
                "index": task.index,
                "output": output,
                "attempt": attempt,
            },
        )

    async def _emit_task_reflected(
        self, run_id: str, task: TaskView, attempt: int, result: ReflectionResult
    ) -> None:
        await self._emitter.emit(
            run_id,
            EventType.TASK_REFLECTED,
            {
                "task_id": task.id,
                "index": task.index,
                "attempt": attempt,
                "decision": result.decision.value,
                "reason": result.reason,
                "confidence": result.confidence,
                "reflection_version": result.reflection_version,
            },
        )

    async def _emit_task_retrying(
        self, run_id: str, task: TaskView, attempt: int, reason: str
    ) -> None:
        await self._emitter.emit(
            run_id,
            EventType.TASK_RETRYING,
            {
                "task_id": task.id,
                "index": task.index,
                "attempt": attempt,
                "reason": reason,
            },
        )

    async def _emit_task_skipped(
        self, run_id: str, task: TaskView, reason: str
    ) -> None:
        await self._emitter.emit(
            run_id,
            EventType.TASK_SKIPPED,
            {"task_id": task.id, "index": task.index, "reason": reason},
        )

    # -- Cancellation cleanup (M4, RFC-0002 §10) ----------------------------- #

    async def _finalize_cancelled_run(
        self, run_id: str, *, cancelled: _Cancelled | None
    ) -> None:
        """Bring every task to a terminal state, then finalize the run CANCELLED.

        The in-flight task (if any) becomes ``CANCELLED`` (with a ``task.cancelled``
        event); every remaining unstarted task becomes ``SKIPPED``. Completed work
        and attempt counts are preserved. Guarantees no task is left
        ``PENDING``/``RUNNING``/``RETRYING`` on the terminal run (invariant I-4).
        """
        if cancelled is not None:
            async with self._db.session() as session:
                await TaskRepository(session).mark_cancelled(cancelled.task.id)
            await self._emitter.emit(
                run_id,
                EventType.TASK_CANCELLED,
                {
                    "task_id": cancelled.task.id,
                    "index": cancelled.task.index,
                    "phase": cancelled.phase,
                },
            )

        await self._skip_pending_tasks(run_id, "run cancelled")
        await self._finalize_cancelled(run_id)

    async def _skip_pending_tasks(self, run_id: str, reason: str) -> None:
        """Mark every still-``PENDING`` task ``SKIPPED`` and emit ``task.skipped``.

        Shared by the cancellation, abort, and budget finalizers so a terminal run
        never leaves an unstarted task behind (invariant I-4).
        """
        async with self._db.session() as session:
            repo = TaskRepository(session)
            remaining = [
                t
                for t in await repo.list_for_run(run_id)
                if t.status is TaskStatus.PENDING
            ]
            await repo.bulk_skip([t.id for t in remaining])
        for task in remaining:
            await self._emit_task_skipped(run_id, task, reason)

    # -- Task-status persistence --------------------------------------------- #

    async def _mark_task_running(self, run_id: str, task_id: str) -> None:
        async with self._db.session() as session:
            await TaskRepository(session).mark_running(task_id)

    async def _mark_task_retrying(self, run_id: str, task_id: str) -> None:
        async with self._db.session() as session:
            await TaskRepository(session).mark_retrying(task_id)

    async def _complete_task(self, run_id: str, task_id: str, output: str) -> None:
        async with self._db.session() as session:
            await TaskRepository(session).mark_done(task_id, output)

    async def _fail_task(self, run_id: str, task: TaskView, error: str) -> None:
        async with self._db.session() as session:
            await TaskRepository(session).mark_failed(task.id, error)
        await self._emitter.emit(
            run_id,
            EventType.TASK_FAILED,
            {"task_id": task.id, "index": task.index, "error": error},
        )

    async def _record_attempt(
        self,
        run_id: str,
        task: TaskView,
        attempt: int,
        output: str | None,
        error: str | None,
    ) -> str:
        """Persist one attempt row and update the task's attempt counter."""
        async with self._db.session() as session:
            view = await TaskAttemptRepository(session).create(
                task_id=task.id,
                run_id=run_id,
                attempt_number=attempt,
                output=output,
                error=error,
            )
            await TaskRepository(session).set_attempt_count(task.id, attempt)
        return view.attempt_id

    async def _record_reflection(
        self, attempt_id: str, result: ReflectionResult
    ) -> None:
        async with self._db.session() as session:
            await TaskAttemptRepository(session).record_reflection(attempt_id, result)

    async def _escalate(
        self,
        run_id: str,
        goal: str,
        task: TaskView,
        result: ReflectionResult | None,
        outputs: list[PriorTaskOutput],
        budget: RunBudget,
        cancel: asyncio.Event,
    ) -> None:
        """Deterministic recovery ladder (RFC-0002 §4): replan, else graceful abort.

        Reaches here on retry-exhaustion (verdict ``retry``), a ``replan`` verdict,
        or an ``abort`` verdict. A ``retry``/``replan`` with a replan remaining
        re-plans the remaining work (raises ``_Replan``); everything else — an
        ``abort``, or retry/replan exhausted — gracefully aborts with a partial
        answer. Cancellation, if observed, takes precedence over the whole ladder.
        """
        if cancel.is_set():
            raise _Cancelled(task, "reflection")
        decision = result.decision if result is not None else None
        if (
            decision in (ReflectionDecision.RETRY, ReflectionDecision.REPLAN)
            and budget.replans_left() > 0
        ):
            raise _Replan(task, result)
        await self._graceful_abort(run_id, goal, task, result, outputs, budget, cancel)

    async def _graceful_abort(
        self,
        run_id: str,
        goal: str,
        task: TaskView,
        result: ReflectionResult | None,
        outputs: list[PriorTaskOutput],
        budget: RunBudget,
        cancel: asyncio.Event,
    ) -> None:
        """Abort/exhaustion: fail the task, skip the rest, synthesize a partial.

        RFC-0002 ADR-0014 / I-12: recovery exhaustion and an ``abort`` verdict
        preserve completed work by synthesizing over the completed ``outputs`` only
        and finalizing the run FAILED *with* a clearly-marked partial answer. A
        budget hit while composing the partial downgrades to FAILED-with-no-partial
        (Amendment 4). Cancellation, if observed, takes precedence.
        """
        if cancel.is_set():
            raise _Cancelled(task, "reflection")
        reason = result.reason if result is not None else "recovery options exhausted"
        error = f"Task {task.index + 1} could not be completed: {reason}"
        await self._fail_task(run_id, task, error)
        await self._skip_pending_tasks(run_id, "run aborted")

        if not outputs:
            await self._finalize_failed(run_id, error)  # nothing to synthesize
            return
        try:
            synthesizer = Synthesizer(
                self._budgeted_gateway(budget, BudgetCategory.SYNTHESIS), self._settings
            )
            partial = await synthesizer.answer(goal, list(outputs))
        except BudgetExceeded as be:
            await self._finalize_budget_exceeded(run_id, be, task=None, cancel=cancel)
            return
        answer = build_partial_answer(partial)
        async with self._db.session() as session:
            await RunRepository(session).set_answer(run_id, answer)
        await self._emitter.emit(run_id, EventType.ANSWER_COMPLETED, {"text": answer})
        await self._finalize_failed(run_id, error)
        # A graceful partial preserves completed work; capture it as a memory
        # (never on a budget/cancel finalize — those have no write call site).
        await self._write_memory(run_id, goal, MemoryOutcome.PARTIAL, answer, budget)

    # -- Budget exhaustion (M4 Phase 8, RFC-0002 §8) ------------------------- #

    async def _finalize_budget_exceeded(
        self,
        run_id: str,
        exc: BudgetExceeded,
        *,
        task: TaskView | None,
        cancel: asyncio.Event,
    ) -> None:
        """Finalize a run that hit a hard model/tool cap: FAILED, no partial answer.

        Emits ``budget.exceeded``, fails the in-flight task (if any), skips the
        remaining pending tasks, and finalizes FAILED — no synthesis (Amendment 4 /
        I-12). Cancellation, if already observed, takes precedence (requirement 6).
        Leaves no task in a non-terminal state (I-4).
        """
        if cancel.is_set():
            await self._finalize_cancelled_run(
                run_id, cancelled=_Cancelled(task, "execution") if task else None
            )
            return
        payload: dict = {"budget": exc.budget, "limit": exc.limit, "used": exc.used}
        if task is not None:
            payload["task_id"] = task.id
        await self._emitter.emit(run_id, EventType.BUDGET_EXCEEDED, payload)
        if task is not None:
            await self._fail_task(run_id, task, f"budget exceeded: {exc.budget}")
        await self._skip_pending_tasks(run_id, "run out of budget")
        await self._finalize_failed(run_id, f"budget exceeded: {exc.budget}")

    def _budgeted_gateway(
        self, budget: RunBudget, category: BudgetCategory
    ) -> LLMGateway:
        """A per-category budget wrapper around the run's gateway (single seam)."""
        return BudgetedGateway(self._gateway, budget, category)

    # -- Episodic memory (M5, RFC-0003) -------------------------------------- #

    async def _write_memory(
        self,
        run_id: str,
        goal: str,
        outcome: MemoryOutcome,
        answer: str,
        budget: RunBudget,
    ) -> None:
        """Distill a memory of the finished run (no-op when memory is disabled)."""
        if self._memory is not None:
            await self._memory.write(run_id, goal, outcome, answer, budget)

    # -- Replanning (M4 Phase 7, RFC-0002 §4/§9.3, ADR-0013) ----------------- #

    async def _do_replan(
        self,
        run_id: str,
        goal: str,
        replan: _Replan,
        remaining: list[TaskView],
        outputs: list[PriorTaskOutput],
        budget: RunBudget,
        cancel: asyncio.Event,
    ) -> list[TaskView] | None:
        """Replan the remaining work and return the new generation's tasks.

        Charges one replan, invokes ``Planner.replan`` over the completed outputs,
        skips the obsolete tasks (the triggering task and everything after it),
        creates the new generation at monotonic indices, and emits ``plan.replanned``.
        Completed tasks and all attempt history are untouched (I-1). Returns the new
        ``PENDING`` tasks, or ``None`` if the replan was cancelled or failed (the run
        is finalized in that case).
        """
        task = replan.task
        if cancel.is_set():  # before Planner.replan (RFC-0002 §10)
            await self._finalize_cancelled_run(
                run_id, cancelled=_Cancelled(task, "replan")
            )
            return None

        budget.charge_replan()
        reason = replan.result.reason if replan.result is not None else "replan requested"
        lessons = self._lessons.get(run_id) or None
        try:
            planned = await Planner(
                self._budgeted_gateway(budget, BudgetCategory.PLANNER),
                self._registry,
                self._settings,
            ).replan(goal, outputs, reason, lessons=lessons)
        except BudgetExceeded as be:
            # The replan model call tripped the hard cap: FAILED, no partial.
            await self._finalize_budget_exceeded(run_id, be, task=task, cancel=cancel)
            return None
        except (PlannerError, LLMError) as exc:
            # A replan that cannot produce a valid plan fails the run. The in-flight
            # task is failed, not orphaned (Phase 8 keeps no orphan RUNNING).
            error = f"Replan failed: {exc}"
            await self._fail_task(run_id, task, error)
            await self._finalize_failed(run_id, error)
            return None

        if cancel.is_set():  # after Planner.replan (RFC-0002 §10)
            await self._finalize_cancelled_run(
                run_id, cancelled=_Cancelled(task, "replan")
            )
            return None

        # Drop the obsolete tasks (triggering task + everything after it). Indices
        # stay monotonic: the new generation appends after the current maximum.
        dropped_ids = [t.id for t in remaining]
        async with self._db.session() as session:
            await TaskRepository(session).bulk_skip(dropped_ids)
        for dropped in remaining:
            await self._emit_task_skipped(run_id, dropped, "replaced by replan")

        generation = budget.replans_used
        parent_generation = generation - 1
        start_index = remaining[-1].index + 1
        async with self._db.session() as session:
            new_tasks = await TaskRepository(session).append_generation(
                run_id,
                planned,
                start_index=start_index,
                generation=generation,
                parent_generation=parent_generation,
            )
        await self._emit_plan_replanned(
            run_id,
            generation=generation,
            parent_generation=parent_generation,
            dropped_ids=dropped_ids,
            new_tasks=new_tasks,
            reason=reason,
        )
        return new_tasks

    async def _emit_plan_replanned(
        self,
        run_id: str,
        *,
        generation: int,
        parent_generation: int | None,
        dropped_ids: list[str],
        new_tasks: list[TaskView],
        reason: str,
    ) -> None:
        await self._emitter.emit(
            run_id,
            EventType.PLAN_REPLANNED,
            {
                "generation": generation,
                "parent_generation": parent_generation,
                "dropped_task_ids": dropped_ids,
                "old_task_count": len(dropped_ids),
                "new_task_count": len(new_tasks),
                "reason": reason,
                "tasks": [_task_brief(t) for t in new_tasks],
            },
        )

    def _cleanup(self, run_id: str) -> None:
        self._tasks.pop(run_id, None)
        self._cancels.pop(run_id, None)
        self._lessons.pop(run_id, None)


def _task_brief(task: TaskView) -> dict:
    """The per-task summary carried in the ``plan.created`` event payload."""
    return {
        "id": task.id,
        "index": task.index,
        "description": task.description,
        "success_criteria": task.success_criteria,
        "suggested_tool": task.suggested_tool,
    }
