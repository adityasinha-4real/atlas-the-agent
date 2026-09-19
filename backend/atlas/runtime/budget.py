"""Per-run failure budgets and the wrappers that enforce them (RFC-0002 §8).

A ``RunBudget`` bounds a single run's work so the run always terminates
(invariant I-10):

* ``model_calls`` and ``tool_calls`` are **hard caps** — charging past them raises
  ``BudgetExceeded``.
* ``planner_calls`` / ``reflection_calls`` / ``synthesis_calls`` are separate
  *category* counters kept for attribution only (Amendment 7); they are not
  themselves capped. Executor model calls are the implicit remainder
  (``model_calls`` minus the three named categories).
* retries-per-task and replans-per-run gate control-flow decisions in the
  RunManager; they are read via ``retries_left`` / ``replans_left`` and advanced
  via ``charge_retry`` / ``charge_replan``.

Enforcement is central via thin wrappers created per run: ``BudgetedGateway``
charges a model call before delegating; ``BudgetedToolRegistry`` charges a tool
call **before** delegating and *outside* the real registry's try/except, so a
``BudgetExceeded`` propagates out of the executor instead of being trapped as a
tool observation (invariant I-9). Only the ``charge_*`` methods mutate; everything
else is a pure read (invariant I-8).

This module is standalone; the RunManager wires these wrappers around its gateway
and registry when the reflect/retry/replan loop is assembled (later phase).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from atlas.core.config import Settings
from atlas.llm.gateway import LLMGateway, LLMMessage
from atlas.tools.base import ToolResult
from atlas.tools.registry import ToolRegistry


class BudgetCategory(StrEnum):
    """Which named counter a model call is attributed to."""

    PLANNER = "planner"
    REFLECTION = "reflection"
    SYNTHESIS = "synthesis"
    MEMORY = "memory"  # M5: post-run distillation (RFC-0003 §9)
    EXECUTOR = "executor"  # implicit: bumps only ``model_calls``


# Category -> the RunBudget attribute it increments (EXECUTOR has none).
_CATEGORY_FIELD: dict[BudgetCategory, str] = {
    BudgetCategory.PLANNER: "planner_calls",
    BudgetCategory.REFLECTION: "reflection_calls",
    BudgetCategory.SYNTHESIS: "synthesis_calls",
    BudgetCategory.MEMORY: "memory_calls",
}


class BudgetExceeded(Exception):
    """A hard budget (model or tool calls) is exhausted.

    Deliberately a plain ``Exception`` (not ``LLMError`` or a tool error) so it is
    not double-handled: it propagates past the tool registry's trap and the LLM
    error handling straight to the RunManager (invariant I-9).
    """

    def __init__(self, budget: str, *, limit: int, used: int) -> None:
        self.budget = budget
        self.limit = limit
        self.used = used
        super().__init__(f"budget exceeded: {budget} (limit={limit}, used={used})")


@dataclass(frozen=True)
class BudgetSnapshot:
    """An immutable read of a ``RunBudget`` at a point in time."""

    model_calls: int
    tool_calls: int
    planner_calls: int
    reflection_calls: int
    synthesis_calls: int
    memory_calls: int
    replans_used: int
    retries_used: dict[str, int]
    max_model_calls: int
    max_tool_calls: int
    max_retries: int
    max_replans: int


@dataclass
class RunBudget:
    """Counts and bounds a single run's model calls, tool calls, retries, replans."""

    max_model_calls: int
    max_tool_calls: int
    max_retries: int
    max_replans: int
    model_calls: int = 0
    tool_calls: int = 0
    planner_calls: int = 0
    reflection_calls: int = 0
    synthesis_calls: int = 0
    memory_calls: int = 0
    replans_used: int = 0
    _retries: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> RunBudget:
        return cls(
            max_model_calls=settings.agent_max_model_calls,
            max_tool_calls=settings.agent_max_tool_calls,
            max_retries=settings.agent_max_retries,
            max_replans=settings.agent_max_replans,
        )

    # -- Mutators (the only write path) -------------------------------------- #

    def charge_model_call(self, category: BudgetCategory) -> None:
        """Count one model call in ``category``. Raises if over the hard cap."""
        if self.model_calls >= self.max_model_calls:
            raise BudgetExceeded(
                "model_calls", limit=self.max_model_calls, used=self.model_calls
            )
        self.model_calls += 1
        field_name = _CATEGORY_FIELD.get(category)
        if field_name is not None:
            setattr(self, field_name, getattr(self, field_name) + 1)

    def charge_tool_call(self) -> None:
        """Count one tool call. Raises if over the hard cap."""
        if self.tool_calls >= self.max_tool_calls:
            raise BudgetExceeded(
                "tool_calls", limit=self.max_tool_calls, used=self.tool_calls
            )
        self.tool_calls += 1

    def charge_retry(self, task_id: str) -> None:
        """Record that a task consumed one retry (control-flow, no hard cap)."""
        self._retries[task_id] = self._retries.get(task_id, 0) + 1

    def charge_replan(self) -> None:
        """Record that the run consumed one replan (control-flow, no hard cap)."""
        self.replans_used += 1

    # -- Pure reads / inspection --------------------------------------------- #

    def retries_left(self, task_id: str) -> int:
        return max(0, self.max_retries - self._retries.get(task_id, 0))

    def replans_left(self) -> int:
        return max(0, self.max_replans - self.replans_used)

    def remaining(self, kind: str) -> int:
        """Headroom on a hard cap (``"model_calls"`` or ``"tool_calls"``)."""
        if kind == "model_calls":
            return max(0, self.max_model_calls - self.model_calls)
        if kind == "tool_calls":
            return max(0, self.max_tool_calls - self.tool_calls)
        raise ValueError(f"Unknown budget kind: {kind!r}")

    def would_exceed(self, kind: str) -> bool:
        """Whether the next charge of ``kind`` would raise ``BudgetExceeded``."""
        return self.remaining(kind) == 0

    def snapshot(self) -> BudgetSnapshot:
        return BudgetSnapshot(
            model_calls=self.model_calls,
            tool_calls=self.tool_calls,
            planner_calls=self.planner_calls,
            reflection_calls=self.reflection_calls,
            synthesis_calls=self.synthesis_calls,
            memory_calls=self.memory_calls,
            replans_used=self.replans_used,
            retries_used=dict(self._retries),
            max_model_calls=self.max_model_calls,
            max_tool_calls=self.max_tool_calls,
            max_retries=self.max_retries,
            max_replans=self.max_replans,
        )


class BudgetedGateway(LLMGateway):
    """Wraps a gateway, charging one model call (in ``category``) per call."""

    def __init__(
        self, inner: LLMGateway, budget: RunBudget, category: BudgetCategory
    ) -> None:
        super().__init__(model=inner.model, temperature=0.0)
        self._inner = inner
        self._budget = budget
        self._category = category

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        self._budget.charge_model_call(self._category)
        async for chunk in self._inner.stream(
            messages, model=model, temperature=temperature
        ):
            yield chunk

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        # Charge before delegating; override complete directly (do not route
        # through self.stream) so a call is counted exactly once.
        self._budget.charge_model_call(self._category)
        return await self._inner.complete(
            messages, model=model, temperature=temperature
        )

    async def health(self) -> bool:
        return await self._inner.health()


class BudgetedToolRegistry(ToolRegistry):
    """Wraps a registry, charging one tool call before each ``execute``.

    The charge happens *outside* the inner registry's try/except, so a
    ``BudgetExceeded`` propagates out of the executor rather than being trapped
    into a ``ToolResult`` observation (invariant I-9). Discovery methods delegate
    unchanged.
    """

    def __init__(self, inner: ToolRegistry, budget: RunBudget) -> None:
        super().__init__()
        self._inner = inner
        self._budget = budget

    def get(self, name: str):  # noqa: ANN201 - matches ToolRegistry.get
        return self._inner.get(name)

    def names(self) -> list[str]:
        return self._inner.names()

    def specs(self):  # noqa: ANN201 - matches ToolRegistry.specs
        return self._inner.specs()

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        self._budget.charge_tool_call()  # may raise BudgetExceeded (not trapped)
        return await self._inner.execute(name, arguments)
