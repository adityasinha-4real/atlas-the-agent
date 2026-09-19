"""The ``LLMGateway`` abstraction and its factory.

The gateway exposes two operations used across milestones:
    * ``complete`` — return a full completion (used by planner/reflector later);
    * ``stream``   — yield text chunks (used by the M1 walking skeleton).

``model`` is a per-call parameter so a future model router is a config change,
not a refactor (design doc §1.8, §7).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Literal

from atlas.core.config import Settings

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class LLMMessage:
    """A single chat message."""

    role: Role
    content: str


class LLMError(RuntimeError):
    """Raised when the underlying provider fails.

    The runtime converts this into an observation/failure event rather than
    letting it escape as an unhandled exception.
    """


class LLMGateway(abc.ABC):
    """Provider-agnostic gateway. Implementations must be async and stateless."""

    def __init__(self, *, model: str, temperature: float) -> None:
        self._model = model
        self._temperature = temperature

    @property
    def model(self) -> str:
        return self._model

    @abc.abstractmethod
    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        """Yield text chunks for the given conversation."""
        raise NotImplementedError

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        """Return a full completion by concatenating the stream.

        Providers may override with a non-streaming call for efficiency.
        """
        chunks: list[str] = []
        async for chunk in self.stream(
            messages, model=model, temperature=temperature
        ):
            chunks.append(chunk)
        return "".join(chunks)

    async def health(self) -> bool:
        """Best-effort readiness probe. Defaults to True."""
        return True

    async def aclose(self) -> None:  # noqa: B027 - optional override hook
        """Release any provider resources. Defaults to no-op."""


def build_gateway(settings: Settings) -> LLMGateway:
    """Construct the configured gateway. The only provider-selection site."""
    provider = settings.llm_provider
    if provider == "ollama":
        from atlas.llm.providers.ollama import OllamaGateway

        return OllamaGateway(
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            temperature=settings.llm_temperature,
            timeout_seconds=settings.llm_timeout_seconds,
        )
    if provider == "echo":
        from atlas.llm.providers.echo import EchoGateway

        return EchoGateway(model=settings.llm_model, temperature=settings.llm_temperature)

    raise LLMError(f"Unknown LLM provider: {provider!r}")
