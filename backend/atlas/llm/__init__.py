"""LLM provider seam.

``LLMGateway`` is the single boundary between ATLAS and any model backend. M1
ships two providers: ``ollama`` (the real local model) and ``echo`` (a
deterministic, dependency-free fake used for development and CI). Selecting a
provider is a config change; call sites never change.
"""

from atlas.llm.gateway import (
    LLMError,
    LLMGateway,
    LLMMessage,
    build_gateway,
)

__all__ = ["LLMError", "LLMGateway", "LLMMessage", "build_gateway"]
