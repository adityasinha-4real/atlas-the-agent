"""Planner — turns a goal into an ordered list of ≤N tasks (design doc §1.1).

The planner makes a single LLM call and parses a flat JSON object
``{"tasks": [...]}`` tolerantly (shared ``jsonio`` extraction + a bounded repair
loop, mirroring the executor). Small models over-produce and emit filler, so the
output is defensively normalized: empty tasks dropped, unknown ``suggested_tool``
hints nulled against the registry, and the list hard-capped. A goal that yields
no usable tasks falls back to a single task equal to the goal — the runtime never
receives an empty plan.

Why a list (not a DAG): tasks run sequentially on a single local model, so there
is no parallelism to exploit; a flat capped list is far more reliable to generate
and reason about (design doc §1.1).
"""

from __future__ import annotations

import json
import logging

from pydantic import ValidationError

from atlas.agent.jsonio import extract_json
from atlas.agent.prompts import (
    build_planner_prompt,
    build_replan_prompt,
    repair_instruction,
    replan_input,
)
from atlas.agent.schemas import Plan, PlannedTask, PriorTaskOutput
from atlas.core.config import Settings
from atlas.llm.gateway import LLMGateway, LLMMessage
from atlas.tools.registry import ToolRegistry

logger = logging.getLogger(__name__)


class PlannerError(Exception):
    """Raised when the planner cannot produce a valid plan (→ run FAILED)."""


class Planner:
    """Produces a normalized, ordered task list for a goal."""

    def __init__(
        self,
        gateway: LLMGateway,
        registry: ToolRegistry,
        settings: Settings,
    ) -> None:
        self._gateway = gateway
        self._registry = registry
        self._max_tasks = settings.agent_max_tasks
        self._repair_attempts = settings.agent_repair_attempts
        self._output_chars = settings.context_prior_output_chars

    async def plan(
        self, goal: str, *, lessons: str | None = None
    ) -> list[PlannedTask]:
        """Plan the goal into 1..N tasks. Raises ``PlannerError`` on bad output.

        ``lessons`` (M5 recall, RFC-0003) is an optional untrusted-hints block
        appended to the system prompt; ``None`` reproduces the pre-M5 prompt.
        """
        system = build_planner_prompt(self._registry.names(), self._max_tasks, lessons)
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=f"Goal: {goal}"),
        ]
        plan = await self._decide(messages)
        return self._normalize(plan.tasks, goal)

    async def replan(
        self,
        goal: str,
        completed: list[PriorTaskOutput],
        reason: str,
        *,
        lessons: str | None = None,
    ) -> list[PlannedTask]:
        """Plan the REMAINING work given completed tasks (RFC-0002 §4, ADR-0013).

        Reuses the same parse/repair/normalize machinery as ``plan`` — the output
        is the same validated, ordered, capped, non-empty task list — but frames
        the model to build on completed work rather than restart it. ``lessons``
        (M5) reuses plan-time recall unchanged. Raises ``PlannerError`` if no valid
        plan can be produced (→ run FAILED).
        """
        system = build_replan_prompt(self._registry.names(), self._max_tasks, lessons)
        messages: list[LLMMessage] = [
            LLMMessage(role="system", content=system),
            LLMMessage(
                role="user",
                content=replan_input(goal, completed, reason, self._output_chars),
            ),
        ]
        plan = await self._decide(messages)
        return self._normalize(plan.tasks, goal)

    # -- Internals ----------------------------------------------------------- #

    async def _decide(self, messages: list[LLMMessage]) -> Plan:
        """Call the model, repairing malformed plans up to the budget."""
        attempt = 0
        turn = list(messages)
        while True:
            raw = await self._gateway.complete(turn)
            plan, error = _parse_plan(raw)
            if plan is not None:
                return plan
            if attempt >= self._repair_attempts:
                raise PlannerError(f"Invalid plan after repair — {error}")
            attempt += 1
            turn = [
                *turn,
                LLMMessage(role="assistant", content=raw),
                LLMMessage(role="user", content=repair_instruction(error)),
            ]

    def _normalize(self, tasks: list[PlannedTask], goal: str) -> list[PlannedTask]:
        """Drop empties, null unknown tool hints, and hard-cap the list."""
        known = set(self._registry.names())
        cleaned: list[PlannedTask] = []
        for task in tasks:
            description = task.description.strip()
            if not description:
                continue
            tool = task.suggested_tool
            if tool is not None and tool not in known:
                logger.info("Planner named unknown tool %r; ignoring hint", tool)
                tool = None
            cleaned.append(
                PlannedTask(
                    description=description,
                    success_criteria=task.success_criteria.strip(),
                    suggested_tool=tool,
                )
            )
            if len(cleaned) >= self._max_tasks:
                break
        if not cleaned:
            # A valid-but-empty plan still needs to do something: run the goal.
            logger.info("Planner returned no usable tasks; falling back to goal")
            cleaned = [PlannedTask(description=goal)]
        return cleaned


def _parse_plan(raw: str) -> tuple[Plan | None, str]:
    """Tolerantly parse a raw completion into a ``Plan``."""
    blob = extract_json(raw)
    if blob is None:
        return None, "No JSON object found in the response."
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return None, f"Malformed JSON: {exc.msg} (at pos {exc.pos})."
    if not isinstance(data, dict):
        return None, "Top-level JSON value must be an object with a 'tasks' list."
    try:
        return Plan.model_validate(data), ""
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "(root)"
        return None, f"Invalid plan shape — {loc}: {first['msg']}."
