"""Ollama provider — the real local model backend (default in production).

Talks to the Ollama HTTP API (``/api/chat``) with streaming enabled. Network and
protocol errors are wrapped as ``LLMError`` so the runtime treats them as
recoverable failures rather than crashes.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence

import httpx

from atlas.llm.gateway import LLMError, LLMGateway, LLMMessage


class OllamaGateway(LLMGateway):
    """Streaming chat gateway backed by a local Ollama server."""

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        temperature: float,
        timeout_seconds: float,
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout_seconds
        self._client = httpx.AsyncClient(base_url=self._base_url, timeout=timeout_seconds)

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        payload = {
            "model": model or self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "options": {
                "temperature": (
                    self._temperature if temperature is None else temperature
                )
            },
        }
        try:
            async with self._client.stream("POST", "/api/chat", json=payload) as resp:
                resp.raise_for_status()
                async for line in resp.aiter_lines():
                    if not line.strip():
                        continue
                    chunk = self._parse_line(line)
                    if chunk:
                        yield chunk
        except httpx.HTTPError as exc:  # connection, timeout, status
            raise LLMError(f"Ollama request failed: {exc}") from exc

    @staticmethod
    def _parse_line(line: str) -> str:
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise LLMError(f"Malformed Ollama stream line: {line!r}") from exc
        if data.get("error"):
            raise LLMError(f"Ollama error: {data['error']}")
        message = data.get("message") or {}
        return message.get("content", "")

    async def health(self) -> bool:
        try:
            resp = await self._client.get("/api/tags")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def aclose(self) -> None:
        await self._client.aclose()
