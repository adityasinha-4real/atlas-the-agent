"""Tool package: the ``Tool`` ABC, the registry, and the P0 tool set.

``build_registry(settings)`` is the single wiring site that assembles the four
P0 tools (design doc §5) into a ready-to-use registry.
"""

from __future__ import annotations

from atlas.core.config import Settings
from atlas.tools.base import Tool, ToolError, ToolResult, ToolSpec
from atlas.tools.calculator import CalculatorTool
from atlas.tools.files import build_file_tools
from atlas.tools.registry import ToolRegistry
from atlas.tools.web_fetch import WebFetchTool
from atlas.tools.web_search import WebSearchTool

__all__ = [
    "Tool",
    "ToolError",
    "ToolResult",
    "ToolSpec",
    "ToolRegistry",
    "build_registry",
]


def build_registry(settings: Settings) -> ToolRegistry:
    """Assemble the P0 tool registry from configuration."""
    registry = ToolRegistry(default_timeout=settings.tool_timeout_seconds)
    registry.register(CalculatorTool())
    registry.register(
        WebSearchTool(max_results=settings.web_search_max_results)
    )
    registry.register(
        WebFetchTool(
            max_chars=settings.web_fetch_max_chars,
            timeout_seconds=settings.tool_timeout_seconds,
        )
    )
    for tool in build_file_tools(settings.workspace_dir):
        registry.register(tool)
    return registry
