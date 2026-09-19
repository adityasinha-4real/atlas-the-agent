"""Reflector: pre-check, LLM verdict parsing, repair/coercion, and fallbacks.

The Reflector is a pure component — every case here asserts only the returned
``ReflectionResult`` (and how many gateway calls happened); nothing is persisted
or emitted. Deterministic via ``ScriptedGateway``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Sequence
from datetime import UTC, datetime

from atlas.agent.prompts import REFLECTION_PROMPT_VERSION
from atlas.agent.reflector import Reflector
from atlas.agent.schemas import (
    ReflectionDecision,
    ReflectionSource,
    TaskStatus,
    TaskView,
)
from atlas.core.config import Settings
from atlas.llm.gateway import LLMError, LLMGateway, LLMMessage
from atlas.llm.providers.scripted import ScriptedGateway

# --- helpers --------------------------------------------------------------- #


def _task(
    description: str = "do the thing", success_criteria: str = "it is done"
) -> TaskView:
    now = datetime.now(UTC)
    return TaskView(
        id="r:0",
        run_id="r",
        index=0,
        description=description,
        success_criteria=success_criteria,
        status=TaskStatus.RUNNING,
        created_at=now,
        updated_at=now,
    )


def _rjson(decision: str, reason: str = "ok", confidence: object = 0.8) -> str:
    return json.dumps(
        {"decision": decision, "reason": reason, "confidence": confidence}
    )


def _reflector(gateway: LLMGateway, **overrides: object) -> Reflector:
    return Reflector(gateway, Settings(environment="test", **overrides))  # type: ignore[arg-type]


class _FailingGateway(LLMGateway):
    """A gateway whose calls always raise ``LLMError`` (provider outage)."""

    def __init__(self) -> None:
        super().__init__(model="failing", temperature=0.0)
        self.calls = 0

    async def stream(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> AsyncIterator[str]:
        yield await self.complete(messages)

    async def complete(
        self,
        messages: Sequence[LLMMessage],
        *,
        model: str | None = None,
        temperature: float | None = None,
    ) -> str:
        self.calls += 1
        raise LLMError("provider is down")


# --- valid responses ------------------------------------------------------- #


async def test_valid_accept_response() -> None:
    gateway = ScriptedGateway([_rjson("accept", "meets criteria", 0.9)])
    result = await _reflector(gateway).reflect(
        goal="g", task=_task(), output="a good result"
    )
    assert result.decision is ReflectionDecision.ACCEPT
    assert result.reason == "meets criteria"
    assert result.confidence == 0.9
    assert result.source is ReflectionSource.LLM
    assert result.reflection_version == REFLECTION_PROMPT_VERSION
    assert len(gateway.calls) == 1


async def test_valid_retry_response_carries_reason() -> None:
    gateway = ScriptedGateway([_rjson("retry", "missing the figure", 0.7)])
    result = await _reflector(gateway).reflect(
        goal="g", task=_task(), output="partial"
    )
    assert result.decision is ReflectionDecision.RETRY
    assert result.reason == "missing the figure"


async def test_decision_is_lowercased_and_prose_wrapped_json_parsed() -> None:
    raw = 'Sure! Here is my verdict:\n{"decision": "REPLAN", "confidence": 0.6}'
    gateway = ScriptedGateway([raw])
    result = await _reflector(gateway).reflect(
        goal="g", task=_task(), output="off track"
    )
    assert result.decision is ReflectionDecision.REPLAN
    assert result.confidence == 0.6


async def test_fenced_json_is_extracted() -> None:
    raw = '```json\n{"decision": "accept", "reason": "fine", "confidence": 0.5}\n```'
    gateway = ScriptedGateway([raw])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="ok")
    assert result.decision is ReflectionDecision.ACCEPT


# --- deterministic pre-check (no LLM call) --------------------------------- #


async def test_precheck_empty_output_retries_without_llm() -> None:
    gateway = ScriptedGateway([])  # any call would raise (exhausted)
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="   ")
    assert result.decision is ReflectionDecision.RETRY
    assert result.source is ReflectionSource.PRECHECK
    assert result.confidence == 0.9
    assert gateway.calls == []


async def test_precheck_error_output_retries_without_llm() -> None:
    gateway = ScriptedGateway([])
    result = await _reflector(gateway).reflect(
        goal="g", task=_task(), output="ERROR: tool exploded"
    )
    assert result.decision is ReflectionDecision.RETRY
    assert result.source is ReflectionSource.PRECHECK
    assert gateway.calls == []


# --- malformed output: repair then fallback -------------------------------- #


async def test_malformed_json_repaired_on_retry() -> None:
    gateway = ScriptedGateway(["not json at all", _rjson("accept")])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.ACCEPT
    assert result.source is ReflectionSource.LLM
    assert len(gateway.calls) == 2  # first failed, repair succeeded


async def test_missing_decision_field_repaired_on_retry() -> None:
    gateway = ScriptedGateway(['{"reason": "no decision here"}', _rjson("retry")])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.RETRY
    assert len(gateway.calls) == 2


async def test_invalid_enum_value_repaired_on_retry() -> None:
    gateway = ScriptedGateway([_rjson("maybe"), _rjson("abort")])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.ABORT
    assert len(gateway.calls) == 2


async def test_all_malformed_falls_back_to_degraded_accept() -> None:
    # default repair_attempts=2 → 1 initial + 2 repairs = 3 calls, all junk.
    gateway = ScriptedGateway(["junk", "still junk", "nope"])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.ACCEPT
    assert result.source is ReflectionSource.DEGRADED
    assert result.confidence == 0.0
    assert len(gateway.calls) == 3


async def test_no_repair_budget_falls_back_immediately() -> None:
    gateway = ScriptedGateway(["junk"])
    result = await _reflector(gateway, agent_repair_attempts=0).reflect(
        goal="g", task=_task(), output="x"
    )
    assert result.source is ReflectionSource.DEGRADED
    assert len(gateway.calls) == 1


# --- confidence coercion / repair ------------------------------------------ #


async def test_confidence_above_one_is_clamped() -> None:
    gateway = ScriptedGateway([_rjson("accept", confidence=1.5)])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 1.0


async def test_confidence_below_zero_is_clamped() -> None:
    gateway = ScriptedGateway([_rjson("accept", confidence=-0.4)])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 0.0


async def test_confidence_string_number_is_parsed() -> None:
    gateway = ScriptedGateway([_rjson("accept", confidence="0.65")])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 0.65


async def test_non_numeric_confidence_defaults_to_half() -> None:
    gateway = ScriptedGateway([_rjson("accept", confidence="high")])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 0.5


async def test_missing_confidence_defaults_to_half() -> None:
    gateway = ScriptedGateway(['{"decision": "accept", "reason": "ok"}'])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 0.5


async def test_boolean_confidence_is_rejected_to_default() -> None:
    gateway = ScriptedGateway(['{"decision": "accept", "confidence": true}'])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.confidence == 0.5


# --- reason handling ------------------------------------------------------- #


async def test_long_reason_is_truncated() -> None:
    gateway = ScriptedGateway([_rjson("accept", reason="x" * 500)])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert len(result.reason) <= 301  # 300 chars + ellipsis
    assert result.reason.endswith("…")


# --- provider failure fallback --------------------------------------------- #


async def test_provider_failure_falls_back_to_degraded_accept() -> None:
    gateway = _FailingGateway()
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.ACCEPT
    assert result.source is ReflectionSource.DEGRADED
    assert gateway.calls == 1  # attempted once, then gave up (no exception)


# --- confidence gate ------------------------------------------------------- #


async def test_confidence_gate_downgrades_low_confidence_retry() -> None:
    gateway = ScriptedGateway([_rjson("retry", "minor", 0.4)])
    result = await _reflector(gateway, agent_reflection_min_confidence=0.7).reflect(
        goal="g", task=_task(), output="x"
    )
    assert result.decision is ReflectionDecision.ACCEPT
    assert "low confidence" in result.reason


async def test_confidence_gate_keeps_confident_retry() -> None:
    gateway = ScriptedGateway([_rjson("retry", "real problem", 0.9)])
    result = await _reflector(gateway, agent_reflection_min_confidence=0.7).reflect(
        goal="g", task=_task(), output="x"
    )
    assert result.decision is ReflectionDecision.RETRY


async def test_confidence_gate_disabled_by_default_keeps_retry() -> None:
    gateway = ScriptedGateway([_rjson("retry", "x", 0.1)])
    result = await _reflector(gateway).reflect(goal="g", task=_task(), output="x")
    assert result.decision is ReflectionDecision.RETRY  # min_confidence=0.0 → no gate


# --- statelessness / schema guarantees ------------------------------------- #


async def test_reflector_is_stateless_across_calls() -> None:
    reflector = _reflector(ScriptedGateway([_rjson("accept"), _rjson("retry")]))
    first = await reflector.reflect(goal="g", task=_task(), output="a")
    second = await reflector.reflect(goal="g", task=_task(), output="b")
    assert first.decision is ReflectionDecision.ACCEPT
    assert second.decision is ReflectionDecision.RETRY


async def test_previous_reasons_appear_in_retry_prompt() -> None:
    gateway = ScriptedGateway([_rjson("accept")])
    await _reflector(gateway).reflect(
        goal="g",
        task=_task(),
        output="x",
        attempt=2,
        previous_reasons=["was missing the citation"],
    )
    user_turn = gateway.calls[0][-1].content
    assert "was missing the citation" in user_turn
    assert "Attempt 2" in user_turn
