"""Tool registry: registration, discovery, and safe execution.

``execute`` is the single choke point through which the executor invokes tools.
It (1) resolves the tool, (2) validates raw arguments against the tool's Pydantic
``Args`` schema, (3) runs it under a timeout, and (4) traps every failure mode
into a ``ToolResult``. This upholds the design's invariant that a tool outcome is
always an observation, never an exception crossing the loop (§1.2c).
"""

from __future__ import annotations

import asyncio
import logging

from pydantic import ValidationError

from atlas.tools.base import Tool, ToolError, ToolResult, ToolSpec

logger = logging.getLogger(__name__)


class ToolRegistry:
    """An ordered collection of tools keyed by name."""

    def __init__(self, *, default_timeout: float = 30.0) -> None:
        self._tools: dict[str, Tool] = {}
        self._default_timeout = default_timeout

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return list(self._tools)

    def specs(self) -> list[ToolSpec]:
        return [type(tool).spec() for tool in self._tools.values()]

    async def execute(self, name: str, arguments: dict) -> ToolResult:
        """Validate, run, and trap. Always returns a ``ToolResult``."""
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self._tools) or "(none)"
            return ToolResult.failure(
                f"Unknown tool {name!r}. Available tools: {available}."
            )

        try:
            args = tool.Args.model_validate(arguments or {})
        except ValidationError as exc:
            # Semantically/schematically wrong args are an observation the model
            # can learn from and retry — not a crash (design doc §1.2a).
            return ToolResult.failure(
                f"Invalid arguments for {name!r}: {_summarize(exc)}"
            )

        try:
            return await asyncio.wait_for(
                tool.run(args), timeout=self._default_timeout
            )
        except TimeoutError:
            return ToolResult.failure(
                f"Tool {name!r} timed out after {self._default_timeout:.0f}s."
            )
        except ToolError as exc:
            return ToolResult.failure(str(exc))
        except Exception as exc:  # defensive: never let a tool break the loop
            logger.exception("Tool %s raised an unexpected error", name)
            return ToolResult.failure(f"Tool {name!r} failed unexpectedly: {exc}")


def _summarize(exc: ValidationError) -> str:
    """Compact a Pydantic validation error into one line for the model."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err["loc"]) or "(root)"
        parts.append(f"{loc}: {err['msg']}")
    return "; ".join(parts)
