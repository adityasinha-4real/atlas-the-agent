"""Recall pipeline: goal → store recall → rendered lessons block (RFC-0003 §7).

``RecallService`` turns a goal into a ``RecallResult``: the selected memories plus
the exact rendered hints block that will be injected into the planner prompt and
recorded in the ``memory.recalled`` event (invariant I-19). Config (``k``,
character budget, minimum relevance) is applied here; ranking/selection lives in
the store.
"""

from __future__ import annotations

from atlas.agent.prompts import format_lessons_block
from atlas.core.config import Settings
from atlas.memory.schemas import RecallResult
from atlas.memory.store import MemoryStore


class RecallService:
    """Plan-time recall + lesson formatting over a ``MemoryStore``."""

    def __init__(self, store: MemoryStore, settings: Settings) -> None:
        self._store = store
        self._k = settings.memory_recall_k
        self._char_budget = settings.memory_recall_char_budget
        self._min_score = settings.memory_recall_min_score

    async def recall(self, goal: str) -> RecallResult:
        """Recall lessons for ``goal``. Empty when recall is off or nothing fits."""
        if self._k <= 0:
            return RecallResult.empty()
        selected = await self._store.recall(
            goal,
            k=self._k,
            char_budget=self._char_budget,
            min_score=self._min_score,
        )
        if not selected:
            return RecallResult.empty()
        rendered = format_lessons_block(
            [(m.memory.goal, m.lesson) for m in selected]
        )
        return RecallResult(memories=selected, rendered=rendered)
