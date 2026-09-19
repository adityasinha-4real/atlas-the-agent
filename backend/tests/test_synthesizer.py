"""Synthesizer: multi-task composition and single-task short-circuit."""

from __future__ import annotations

from atlas.agent.schemas import PriorTaskOutput
from atlas.agent.synthesizer import Synthesizer
from atlas.core.config import Settings
from atlas.llm.providers.scripted import ScriptedGateway


async def test_combines_multiple_task_outputs() -> None:
    gateway = ScriptedGateway(["The final composed answer."])
    synth = Synthesizer(gateway, Settings(environment="test"))

    result = await synth.answer(
        "goal",
        [
            PriorTaskOutput(index=0, description="a", output="alpha"),
            PriorTaskOutput(index=1, description="b", output="beta"),
        ],
    )

    assert result == "The final composed answer."
    # The synthesizer saw both outputs in its prompt.
    prompt = gateway.calls[0][-1].content
    assert "alpha" in prompt and "beta" in prompt


async def test_single_task_short_circuits_without_llm_call() -> None:
    gateway = ScriptedGateway([])  # any call would raise (exhausted)
    synth = Synthesizer(gateway, Settings(environment="test"))

    result = await synth.answer(
        "goal", [PriorTaskOutput(index=0, description="only", output="the output")]
    )

    assert result == "the output"
    assert gateway.calls == []  # no LLM call made


async def test_single_task_synthesis_can_be_forced() -> None:
    gateway = ScriptedGateway(["forced synthesis"])
    synth = Synthesizer(
        gateway, Settings(environment="test", agent_force_synthesis=True)
    )

    result = await synth.answer(
        "goal", [PriorTaskOutput(index=0, description="only", output="raw")]
    )

    assert result == "forced synthesis"
    assert len(gateway.calls) == 1


async def test_empty_outputs_returns_empty() -> None:
    gateway = ScriptedGateway([])
    synth = Synthesizer(gateway, Settings(environment="test"))

    assert await synth.answer("goal", []) == ""
    assert gateway.calls == []
