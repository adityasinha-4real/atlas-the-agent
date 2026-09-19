"""Deterministic ``echo`` provider — a dependency-free fake model.

Used for local development without Ollama and for CI (no model required). It
produces a stable, inspectable answer derived from the last user message, and
streams it word-by-word so the WS/event pipeline is exercised realistically.

This is also the seed of the "FakeLLM" the design doc mandates from M2 onward.
From M3 the runtime plans before it executes, so echo is planner-aware: when it
sees a planner prompt it returns a valid single-task plan (the task is the goal).
That single task then runs through the executor — echo's non-JSON answer degrades
gracefully to the task output (ADR-0008) and synthesis short-circuits — so a full
plan → execute → answer cycle still completes without a real model. The strict
planner contract is exercised separately by the scripted FakeLLM tests.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Sequence

from atlas.llm.gateway import LLMGateway, LLMMessage

# Unique to the planner system prompt (it asks for a top-level ``"tasks"`` array);
# neither the executor nor the synthesis prompt contains the quoted token.
_PLANNER_SIGNAL = '"tasks"'
_GOAL_PREFIX = "Goal: "


class EchoGateway(LLMGateway):
    """Echoes a deterministic response built from the conversation."""

    def __init__(
        self,
        *,
        model: str = "echo",
        temperature: float = 0.0,
        chunk_delay_seconds: float = 0.0,
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self._chunk_delay = chunk_delay_seconds

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        text = self._render(messages)
        words = text.split(" ")
        for index, word in enumerate(words):
            if self._chunk_delay:
                await asyncio.sleep(self._chunk_delay)
            yield word if index == 0 else f" {word}"

    @staticmethod
    def _render(messages: Sequence[LLMMessage]) -> str:
        system = " ".join(m.content for m in messages if m.role == "system")
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"),
            "",
        )
        if _PLANNER_SIGNAL in system:
            return EchoGateway._render_plan(last_user)
        return f"[echo] You asked: {last_user}"

    @staticmethod
    def _render_plan(last_user: str) -> str:
        """Return a valid single-task plan whose one task is the goal."""
        goal = last_user
        if goal.startswith(_GOAL_PREFIX):
            goal = goal[len(_GOAL_PREFIX) :]
        goal = goal.strip() or "Answer the goal."
        return json.dumps(
            {
                "tasks": [
                    {
                        "description": goal,
                        "success_criteria": "",
                        "suggested_tool": None,
                    }
                ]
            }
        )
