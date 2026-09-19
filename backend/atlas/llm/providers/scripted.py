"""``ScriptedGateway`` — the FakeLLM for deterministic agent tests (design §10.5).

It replays a fixed sequence of completions regardless of the prompt, so the
executor's ReAct loop can be driven through exact tool-call / finish / malformed
paths with no model or network. Each ``complete``/``stream`` call consumes the
next scripted response; running past the end raises, surfacing test-script drift.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence

from atlas.llm.gateway import LLMGateway, LLMMessage


class ScriptedGateway(LLMGateway):
    """A gateway that returns pre-programmed responses in order."""

    def __init__(
        self,
        responses: Sequence[str],
        *,
        model: str = "scripted",
        temperature: float = 0.0,
    ) -> None:
        super().__init__(model=model, temperature=temperature)
        self._responses = list(responses)
        self._index = 0
        self.calls: list[list[LLMMessage]] = []

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        yield await self.complete(messages, model=model, temperature=temperature)

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        self.calls.append(list(messages))
        if self._index >= len(self._responses):
            raise AssertionError(
                "ScriptedGateway exhausted: the agent made more LLM calls than "
                f"the {len(self._responses)} scripted responses."
            )
        response = self._responses[self._index]
        self._index += 1
        return response

    @property
    def remaining(self) -> int:
        return len(self._responses) - self._index
