"""Synthesizer — composes the final answer from task outputs (ADR-0010).

Multi-step goals need their answer *composed* across task outputs rather than
taking the last task's output verbatim, so synthesis is a distinct LLM call over
the goal + ordered outputs. A single-task plan degrades cleanly: by default it
short-circuits to that task's output (no extra LLM call, RFC-0001 decision 1),
unless ``agent_force_synthesis`` is set — the seam that lets a future milestone
always synthesize.

The synthesizer invokes no tools and is instructed to treat task outputs as data,
not instructions (prompt-injection mitigation, RFC §14).
"""

from __future__ import annotations

from atlas.agent.prompts import build_synthesis_prompt, synthesis_input
from atlas.agent.schemas import PriorTaskOutput
from atlas.core.config import Settings
from atlas.llm.gateway import LLMGateway, LLMMessage


class Synthesizer:
    """Produces a run's final answer from its completed task outputs."""

    def __init__(self, gateway: LLMGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._force = settings.agent_force_synthesis

    async def answer(self, goal: str, outputs: list[PriorTaskOutput]) -> str:
        """Compose the final answer. May short-circuit for single-task plans."""
        if not outputs:
            # Defensive: the runtime never calls this with an empty plan.
            return ""
        if len(outputs) == 1 and not self._force:
            return outputs[0].output

        messages = [
            LLMMessage(role="system", content=build_synthesis_prompt()),
            LLMMessage(role="user", content=synthesis_input(goal, outputs)),
        ]
        return (await self._gateway.complete(messages)).strip()
