"""Context Builder — assembles a task's executor prompt (design doc §1.11).

Given a typed ``TaskContext`` (goal + current task + prior task outputs), the
builder produces the exact ``list[LLMMessage]`` the executor runs on: the shared
system prompt (envelope rules + available tools) followed by a task-framing user
turn. The executor is kept execution-only — it never constructs context itself
(RFC-0001 decision 4).

Prior task outputs are truncated per ``context_prior_output_chars`` so prompt
size stays roughly constant across tasks (summarize-at-source, §1.11) rather than
growing linearly with plan length.
"""

from __future__ import annotations

from atlas.agent.prompts import build_critique, build_system_prompt, build_task_prompt
from atlas.agent.schemas import PriorTaskOutput, TaskContext
from atlas.core.config import Settings
from atlas.llm.gateway import LLMMessage
from atlas.tools.registry import ToolRegistry

_ELLIPSIS = "…"


class ContextBuilder:
    """Turns a ``TaskContext`` into executor-ready messages."""

    def __init__(self, registry: ToolRegistry, settings: Settings) -> None:
        self._registry = registry
        self._max_chars = settings.context_prior_output_chars

    def build(self, context: TaskContext) -> list[LLMMessage]:
        system = build_system_prompt(self._registry.specs())
        priors = [
            PriorTaskOutput(
                index=prior.index,
                description=prior.description,
                output=_truncate(prior.output, self._max_chars),
            )
            for prior in context.prior_outputs
        ]
        user = build_task_prompt(
            goal=context.goal,
            task=context.task,
            prior_outputs=priors,
            total_tasks=context.total_tasks,
        )
        # On a retry, fold in a critique of the previous attempt (M4).
        if context.attempt > 1 and context.critique_reason:
            critique = build_critique(
                _truncate(context.previous_output or "", self._max_chars),
                context.critique_reason,
            )
            user = f"{user}\n\n{critique}"
        return [
            LLMMessage(role="system", content=system),
            LLMMessage(role="user", content=user),
        ]


def _truncate(text: str, max_chars: int) -> str:
    """Clip an output to the budget, marking that it was shortened."""
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 1].rstrip() + _ELLIPSIS
