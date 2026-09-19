"""Distill a finished run into a ``MemoryRecord`` (RFC-0003 §6, ADR-0016).

An LLM call compresses the run's goal, outcome, plan, and answer into a short
``summary`` + transferable ``lessons``. Distillation is best-effort: any failure
(model error, unparseable output, exhausted budget) falls back to a zero-LLM
heuristic so a memory is still captured and the run is never affected (I-14). Only
distilled text is stored — never raw tool output (I-18).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from atlas.agent.jsonio import extract_json
from atlas.agent.prompts import build_memory_distill_prompt, memory_distill_input
from atlas.core.config import Settings
from atlas.llm.gateway import LLMGateway, LLMMessage
from atlas.memory.schemas import MemoryOutcome, MemoryRecord, MemorySource

logger = logging.getLogger(__name__)

_MAX_SUMMARY_CHARS = 400
_MAX_LESSONS_CHARS = 600


class MemoryWriter:
    """Turns a finished run into a distilled ``MemoryRecord``."""

    def __init__(self, settings: Settings) -> None:
        self._distill_with_llm = settings.memory_distill_with_llm
        self._output_chars = settings.context_prior_output_chars

    async def distill(
        self,
        *,
        goal: str,
        outcome: MemoryOutcome,
        answer: str,
        task_descriptions: Sequence[str],
        tools_used: Sequence[str],
        task_count: int,
        run_id: str | None,
        gateway: LLMGateway | None,
    ) -> MemoryRecord:
        """Distill a memory. Never raises — falls back to a heuristic on failure."""
        summary = ""
        lessons = ""
        source = MemorySource.HEURISTIC
        if self._distill_with_llm and gateway is not None:
            try:
                summary, lessons = await self._llm_distill(
                    gateway, goal, outcome, answer, task_descriptions
                )
                source = MemorySource.SYNTHESIZED
            except Exception:  # noqa: BLE001 - best-effort; degrade to heuristic
                logger.warning(
                    "Memory distillation failed; using heuristic", exc_info=True
                )
                summary, lessons = _heuristic(goal, outcome, tools_used)
        else:
            summary, lessons = _heuristic(goal, outcome, tools_used)

        return MemoryRecord(
            run_id=run_id,
            goal=goal,
            outcome=outcome,
            summary=summary[:_MAX_SUMMARY_CHARS],
            lessons=lessons[:_MAX_LESSONS_CHARS],
            tools_used=list(tools_used),
            task_count=task_count,
            source=source,
        )

    async def _llm_distill(
        self,
        gateway: LLMGateway,
        goal: str,
        outcome: MemoryOutcome,
        answer: str,
        task_descriptions: Sequence[str],
    ) -> tuple[str, str]:
        messages = [
            LLMMessage(role="system", content=build_memory_distill_prompt()),
            LLMMessage(
                role="user",
                content=memory_distill_input(
                    goal,
                    outcome.value,
                    answer,
                    list(task_descriptions),
                    self._output_chars,
                ),
            ),
        ]
        raw = await gateway.complete(messages)
        blob = extract_json(raw)
        if blob is None:
            raise ValueError("no JSON object in distillation response")
        data = json.loads(blob)
        if not isinstance(data, dict):
            raise ValueError("distillation response is not a JSON object")
        summary = str(data.get("summary", "")).strip()
        lessons = str(data.get("lessons", "")).strip()
        return summary, lessons


def _heuristic(
    goal: str, outcome: MemoryOutcome, tools_used: Sequence[str]
) -> tuple[str, str]:
    """A zero-LLM fallback: state the outcome and the tool approach that ran."""
    short_goal = goal.strip().replace("\n", " ")
    if len(short_goal) > 160:
        short_goal = short_goal[:160].rstrip() + "…"
    summary = f"Attempted '{short_goal}' — outcome: {outcome.value}."
    if tools_used:
        lessons = f"Approach: use {' then '.join(tools_used)}."
    else:
        lessons = ""
    return summary, lessons
