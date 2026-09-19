"""Tool abstraction: the plugin seam for agent capabilities (design doc §1.6).

A ``Tool`` is an ABC with a Pydantic ``Args`` schema, a human-readable
description (shown to the planner/executor), and an async ``run``. Execution is
mediated by the registry, which validates arguments, enforces a timeout, and
converts *every* outcome — success, bad args, timeout, crash — into a
``ToolResult``. No tool exception ever crosses the executor loop boundary
(design doc §1.2c).
"""

from __future__ import annotations

import abc
from typing import ClassVar

from pydantic import BaseModel


class ToolError(RuntimeError):
    """Raised inside a tool to signal an expected, recoverable failure.

    The registry turns this (and any other exception) into a failed
    ``ToolResult`` observation rather than propagating it.
    """


class ToolResult(BaseModel):
    """The outcome of a tool invocation — always an observation, never a raise."""

    ok: bool
    output: str = ""
    error: str | None = None

    model_config = {"frozen": True}

    @classmethod
    def success(cls, output: str) -> ToolResult:
        return cls(ok=True, output=output)

    @classmethod
    def failure(cls, error: str) -> ToolResult:
        return cls(ok=False, error=error)

    def as_observation(self) -> str:
        """Render for inclusion in the executor's context window."""
        if self.ok:
            return self.output
        return f"ERROR: {self.error}"


class Tool(abc.ABC):
    """Base class for all tools.

    Subclasses declare ``name``, ``description``, and an ``Args`` Pydantic model,
    then implement ``run``. Argument validation, timeouts, and error trapping are
    the registry's job, so ``run`` can assume it receives a valid ``Args``.
    """

    name: ClassVar[str]
    description: ClassVar[str]
    Args: ClassVar[type[BaseModel]]

    @abc.abstractmethod
    async def run(self, args: BaseModel) -> ToolResult:
        """Execute the tool. Prefer returning ``ToolResult.failure`` or raising
        ``ToolError`` for expected failures; unexpected exceptions are trapped."""
        raise NotImplementedError

    @classmethod
    def spec(cls) -> ToolSpec:
        """The planner/executor-facing description of this tool."""
        return ToolSpec(
            name=cls.name,
            description=cls.description,
            args_schema=cls.Args.model_json_schema(),
        )


class ToolSpec(BaseModel):
    """A tool's public contract, surfaced to the model and the ``GET /tools`` API."""

    name: str
    description: str
    args_schema: dict
