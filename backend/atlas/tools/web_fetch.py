"""``web_fetch`` — retrieve a URL and extract readable text (summarize-at-source).

The response body is stripped to plain text and truncated at write time to a
configured character budget (design doc §1.11: summarize-at-source bounds both
context and storage). HTML parsing uses ``lxml`` when available and degrades to a
minimal tag stripper otherwise.
"""

from __future__ import annotations

import re
from typing import ClassVar

import httpx
from pydantic import BaseModel, Field

from atlas.tools.base import Tool, ToolError, ToolResult

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKLINES_RE = re.compile(r"\n\s*\n+")
_DROP_RE = re.compile(
    r"<(script|style|noscript)[^>]*>.*?</\1>", re.DOTALL | re.IGNORECASE
)


class WebFetchArgs(BaseModel):
    url: str = Field(description="Absolute http(s) URL to fetch.", min_length=1)


class WebFetchTool(Tool):
    name: ClassVar[str] = "web_fetch"
    description: ClassVar[str] = (
        "Fetch a web page and return its readable text content (truncated). Pass a "
        "full http(s) URL, typically one returned by web_search."
    )
    Args: ClassVar[type[BaseModel]] = WebFetchArgs

    def __init__(self, max_chars: int = 2000, timeout_seconds: float = 20.0) -> None:
        self._max_chars = max_chars
        self._timeout = timeout_seconds

    async def run(self, args: BaseModel) -> ToolResult:
        assert isinstance(args, WebFetchArgs)
        if not args.url.lower().startswith(("http://", "https://")):
            raise ToolError("URL must start with http:// or https://.")
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout, follow_redirects=True
            ) as client:
                resp = await client.get(args.url, headers={"User-Agent": "ATLAS/0.2"})
                resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolError(f"Failed to fetch {args.url!r}: {exc}") from exc

        text = _extract_text(resp.text)
        if not text:
            return ToolResult.success("(page had no extractable text)")
        if len(text) > self._max_chars:
            text = text[: self._max_chars].rstrip() + "\n…[truncated]"
        return ToolResult.success(text)


def _extract_text(html: str) -> str:
    """Best-effort HTML → text. Uses lxml if present, else a regex stripper."""
    try:
        from lxml import html as lxml_html  # type: ignore[import-untyped]

        tree = lxml_html.fromstring(html)
        for bad in tree.xpath("//script | //style | //noscript"):
            bad.getparent().remove(bad)
        text = tree.text_content()
    except Exception:  # missing lxml or unparseable markup → regex fallback
        stripped = _DROP_RE.sub(" ", html)
        text = _TAG_RE.sub(" ", stripped)

    text = _WS_RE.sub(" ", text)
    text = _BLANKLINES_RE.sub("\n\n", text)
    return text.strip()
