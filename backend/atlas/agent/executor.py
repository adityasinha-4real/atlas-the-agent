"""Executor — the per-task ReAct loop (design doc §1.2, the heart of the agent).

The executor is *execution-only* (RFC-0001 decision 4): it receives a prebuilt
message list from the Context Builder and runs the loop, never constructing task
context itself. Each iteration the model emits a JSON action envelope (with a
bounded repair loop for malformed output); ATLAS either calls a tool and feeds
the observation back, or finishes with an answer. Invariants held here:

* Every tool outcome is an observation — no tool exception crosses the loop
  (enforced by ``ToolRegistry.execute``).
* Cancellation is cooperative, checked between iterations.
* If the model never emits a valid envelope for a turn (after repair), its raw
  text is accepted as a final answer rather than crashing the run — a graceful
  degradation documented in ADR-0008.
* From M3 the emitted ``thought``/``tool.call``/``tool.result`` events carry the
  current ``task_id`` so the UI can group them under the right plan task.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence

from atlas.agent.envelope import AgentAction, parse_action
from atlas.agent.prompts import observation_message, repair_instruction
from atlas.core.config import Settings
from atlas.events.emit import EventEmitter
from atlas.events.types import EventType
from atlas.llm.gateway import LLMGateway, LLMMessage
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class RunCancelled(Exception):
    """Raised inside the executor when cancellation is requested."""


class ExecutorError(Exception):
    """Raised when the loop cannot converge within its iteration budget."""


class Executor:
    """Runs a prebuilt task context to an answer via the ReAct loop."""

    def __init__(
        self,
        gateway: LLMGateway,
        registry: ToolRegistry,
        emitter: EventEmitter,
        settings: Settings,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._emitter = emitter
        self._max_iters = settings.agent_max_iterations
        self._repair_attempts = settings.agent_repair_attempts

    async def run(
        self,
        run_id: str,
        messages: Sequence[LLMMessage],
        cancel: asyncio.Event,
        *,
        task_id: str | None = None,
        attempt: int = 1,
        generation: int = 0,
    ) -> str:
        """Execute the prebuilt context and return the final answer text.

        ``attempt``/``generation`` (M4, RFC-0002 §6) are stamped onto the emitted
        ``thought``/``tool.*`` events so the UI can group a task's events by the
        attempt (and plan generation) that produced them. They are attached only
        when a ``task_id`` is present (an agentic run); a bare M2 executor call
        emits the same untagged events as before.
        """
        messages = list(messages)

        for _iteration in range(self._max_iters):
            if cancel.is_set():
                raise RunCancelled

            action, raw = await self._decide(messages)
            messages.append(LLMMessage(role="assistant", content=raw))

            if action is None:
                # Repair exhausted: accept the narration as the answer.
                logger.info("Run %s: unparseable turn accepted as answer", run_id)
                await self._emit_thought(
                    run_id, "(answering directly)", task_id, attempt, generation
                )
                return raw.strip()

            await self._emit_thought(
                run_id, action.thought, task_id, attempt, generation
            )

            if action.is_finish:
                return action.answer or ""

            observation = await self._invoke_tool(
                run_id, action, task_id, attempt, generation
            )
            messages.append(
                LLMMessage(
                    role="user",
                    content=observation_message(action.tool or "", observation),
                )
            )

        raise ExecutorError(
            f"Agent did not finish within {self._max_iters} iterations."
        )

    # -- Internals ----------------------------------------------------------- #

    async def _decide(
        self, messages: list[LLMMessage]
    ) -> tuple[AgentAction | None, str]:
        """Get the next action, repairing malformed JSON up to the budget.

        On exhaustion we return the model's *first* response, not the last: the
        first attempt holds its genuine content, while later turns only chase the
        JSON format. This is what the graceful-degradation path answers with.
        """
        attempt = 0
        turn = list(messages)
        first_raw = ""
        while True:
            raw = await self._gateway.complete(turn)
            if attempt == 0:
                first_raw = raw
            result = parse_action(raw)
            if result.ok:
                return result.action, raw
            if attempt >= self._repair_attempts:
                return None, first_raw
            attempt += 1
            turn = [
                *turn,
                LLMMessage(role="assistant", content=raw),
                LLMMessage(role="user", content=repair_instruction(result.error or "")),
            ]

    async def _invoke_tool(
        self,
        run_id: str,
        action: AgentAction,
        task_id: str | None,
        attempt: int,
        generation: int,
    ) -> str:
        await self._emitter.emit(
            run_id,
            EventType.TOOL_CALL,
            _tag(
                {"tool": action.tool, "arguments": action.arguments},
                task_id,
                attempt,
                generation,
            ),
        )
        result = await self._registry.execute(action.tool or "", action.arguments)
        await self._emitter.emit(
            run_id,
            EventType.TOOL_RESULT,
            _tag(
                {
                    "tool": action.tool,
                    "ok": result.ok,
                    "observation": result.as_observation(),
                },
                task_id,
                attempt,
                generation,
            ),
        )
        return result.as_observation()

    async def _emit_thought(
        self,
        run_id: str,
        thought: str,
        task_id: str | None,
        attempt: int,
        generation: int,
    ) -> None:
        if thought:
            await self._emitter.emit(
                run_id,
                EventType.THOUGHT,
                _tag({"text": thought}, task_id, attempt, generation),
            )


def _tag(payload: dict, task_id: str | None, attempt: int, generation: int) -> dict:
    """Attribute an executor event to its task, attempt, and plan generation (M4).

    ``task_id`` was added in M3; ``attempt``/``generation`` in M4. They are only
    attached for task-scoped runs (``task_id`` present) so a bare M2 executor call
    keeps emitting the original untagged payloads.
    """
    if task_id is not None:
        payload["task_id"] = task_id
        payload["attempt"] = attempt
        payload["generation"] = generation
    return payload
