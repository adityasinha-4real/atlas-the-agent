"""Echo provider determinism and streaming behavior."""

from __future__ import annotations

from atlas.core.config import Settings
from atlas.llm.gateway import LLMMessage, build_gateway
from atlas.llm.providers.echo import EchoGateway


def _messages(goal: str) -> list[LLMMessage]:
    return [
        LLMMessage(role="system", content="sys"),
        LLMMessage(role="user", content=goal),
    ]


async def test_complete_is_deterministic() -> None:
    gw = EchoGateway()
    out1 = await gw.complete(_messages("hello world"))
    out2 = await gw.complete(_messages("hello world"))
    assert out1 == out2
    assert "hello world" in out1


async def test_stream_chunks_concatenate_to_complete() -> None:
    gw = EchoGateway()
    chunks = [c async for c in gw.stream(_messages("a b c"))]
    assert len(chunks) > 1  # streamed word by word
    assert "".join(chunks) == await gw.complete(_messages("a b c"))


def test_factory_selects_echo() -> None:
    gw = build_gateway(Settings(llm_provider="echo"))
    assert isinstance(gw, EchoGateway)
