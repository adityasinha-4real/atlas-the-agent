"""``web_search`` — grounds the agent in live data via DuckDuckGo (``ddgs``).

``ddgs`` is a synchronous library, so the blocking call runs in a worker thread.
If the package is missing or the network fails, the tool returns a failed
observation rather than raising — the executor treats it like any other tool
outcome.
"""

from __future__ import annotations

import asyncio
from typing import ClassVar

from pydantic import BaseModel, Field

from atlas.tools.base import Tool, ToolError, ToolResult


class WebSearchArgs(BaseModel):
    query: str = Field(description="The search query.", min_length=1, max_length=400)
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchTool(Tool):
    name: ClassVar[str] = "web_search"
    description: ClassVar[str] = (
        "Search the web and return the top results (title, URL, snippet). Use this "
        "to find pages, then web_fetch to read one."
    )
    Args: ClassVar[type[BaseModel]] = WebSearchArgs

    def __init__(self, max_results: int = 5) -> None:
        self._cap = max_results

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, WebSearchArgs)
        limit = min(args.max_results, self._cap)
        results = await asyncio.to_thread(self._search, args.query, limit)
        if not results:
            return ToolResult.success("No results found.")
        lines = [
            f"{i}. {r.get('title', '(no title)')}\n   {r.get('href', '')}\n"
            f"   {r.get('body', '')}"
            for i, r in enumerate(results, start=1)
        ]
        return ToolResult.success("\n".join(lines))

    @staticmethod
    def _search(query: str, limit: int) -> list[dict]:
        try:
            from ddgs import DDGS
        except ImportError as exc:  # pragma: no cover - depends on env
            raise ToolError(
                "web_search is unavailable: the 'ddgs' package is not installed."
            ) from exc
        try:
            with DDGS() as ddgs:
                return list(ddgs.text(query, max_results=limit))
        except Exception as exc:  # network, rate limit, parsing
            raise ToolError(f"Web search failed: {exc}") from exc
