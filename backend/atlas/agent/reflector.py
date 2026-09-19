"""Reflector — judges a task attempt and returns a validated ``ReflectionResult``.

A pure, stateless component (RFC-0002 §7, Amendment 10 / invariant I-7): given a
goal, a task, and the attempt's output, it returns a ``ReflectionResult`` and
mutates nothing — no run/task state, no persistence, no events. Its shape is:

    output → deterministic pre-check → (LLM verdict → parse → validate/repair)
           → ReflectionResult

A deterministic pre-check handles the cheap, high-precision cases (empty or
``ERROR:`` output → retry) with no model call. Otherwise it asks the reflector
model for a verdict, parses it tolerantly (shared ``jsonio`` + a bounded repair
loop, mirroring the planner), and coerces the fields against the schema. When the
model cannot produce a usable verdict — unparseable after repair, or the provider
itself fails — it falls back to ``accept`` (acceptance bias, kin to ADR-0008: a
small model's unbounded self-doubt is worse than a bounded, mediocre answer).

Budgets, escalation (retry → replan → abort), persistence, and event emission are
the RunManager's responsibility in later phases; the Reflector only decides.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence

from pydantic import ValidationError

from atlas.agent.jsonio import extract_json
from atlas.agent.prompts import (
    REFLECTION_PROMPT_VERSION,
    build_reflection_prompt,
    reflection_input,
    repair_instruction,
)
from atlas.agent.schemas import (
    ReflectionDecision,
    ReflectionResult,
    ReflectionSource,
    TaskView,
)
from atlas.core.config import Settings
from atlas.llm.gateway import LLMError, LLMGateway, LLMMessage

logger = logging.getLogger(__name__)

_MAX_REASON_CHARS = 300
_DEFAULT_CONFIDENCE = 0.5
_DEGRADABLE = (ReflectionDecision.RETRY, ReflectionDecision.REPLAN)


class Reflector:
    """Judges a completed task attempt and returns a verdict (RFC-0002 §7)."""

    def __init__(self, gateway: LLMGateway, settings: Settings) -> None:
        self._gateway = gateway
        self._repair_attempts = settings.agent_repair_attempts
        self._output_chars = settings.context_prior_output_chars
        self._min_confidence = settings.agent_reflection_min_confidence

    async def reflect(
        self,
        *,
        goal: str,
        task: TaskView,
        output: str,
        attempt: int = 1,
        previous_reasons: Sequence[str] = (),
    ) -> ReflectionResult:
        """Return a validated verdict for ``output``. Never raises."""
        # 1) Deterministic pre-check — no LLM call.
        precheck = _precheck(output)
        if precheck is not None:
            return precheck

        # 2) LLM verdict, tolerant parse + bounded repair.
        try:
            result = await self._llm_verdict(
                goal, task, output, attempt, previous_reasons
            )
        except LLMError as exc:
            logger.warning("Reflection call failed (%s); accepting (bias)", exc)
            return _degraded("reflection unavailable; accepting (bias)")

        if result is None:  # unparseable after repair
            return _degraded("reflection unparseable; accepting (bias)")

        # 3) Confidence gate — downgrade an under-confident retry/replan to accept.
        return self._apply_confidence_gate(result)

    # -- Internals ----------------------------------------------------------- #

    async def _llm_verdict(
        self,
        goal: str,
        task: TaskView,
        output: str,
        attempt: int,
        previous_reasons: Sequence[str],
    ) -> ReflectionResult | None:
        """Call the model, repairing malformed verdicts up to the budget."""
        turn: list[LLMMessage] = [
            LLMMessage(role="system", content=build_reflection_prompt()),
            LLMMessage(
                role="user",
                content=reflection_input(
                    goal, task, output, attempt, previous_reasons, self._output_chars
                ),
            ),
        ]
        repairs = 0
        while True:
            raw = await self._gateway.complete(turn)
            result, error = _parse_reflection(raw)
            if result is not None:
                return result
            if repairs >= self._repair_attempts:
                return None
            repairs += 1
            turn = [
                *turn,
                LLMMessage(role="assistant", content=raw),
                LLMMessage(role="user", content=repair_instruction(error)),
            ]

    def _apply_confidence_gate(self, result: ReflectionResult) -> ReflectionResult:
        """Below the confidence floor, a retry/replan becomes an accept."""
        if (
            self._min_confidence > 0.0
            and result.decision in _DEGRADABLE
            and result.confidence < self._min_confidence
        ):
            return ReflectionResult(
                decision=ReflectionDecision.ACCEPT,
                reason="low confidence; accepting to avoid churn",
                confidence=result.confidence,
                source=result.source,
                reflection_version=result.reflection_version,
            )
        return result


# --------------------------------------------------------------------------- #
# Pure helpers
# --------------------------------------------------------------------------- #


def _precheck(output: str) -> ReflectionResult | None:
    """Deterministic gates that need no model call (RFC-0002 §7.1)."""
    stripped = output.strip()
    if not stripped or stripped.startswith("ERROR:"):
        return ReflectionResult(
            decision=ReflectionDecision.RETRY,
            reason="empty or error output; retry",
            confidence=0.9,
            source=ReflectionSource.PRECHECK,
            reflection_version=REFLECTION_PROMPT_VERSION,
        )
    return None


def _degraded(reason: str) -> ReflectionResult:
    """Acceptance-bias fallback when no usable verdict could be produced."""
    return ReflectionResult(
        decision=ReflectionDecision.ACCEPT,
        reason=reason,
        confidence=0.0,
        source=ReflectionSource.DEGRADED,
        reflection_version=REFLECTION_PROMPT_VERSION,
    )


def _parse_reflection(raw: str) -> tuple[ReflectionResult | None, str]:
    """Tolerantly parse a raw completion into a ``ReflectionResult``.

    Returns ``(result, "")`` on success or ``(None, error)`` describing why it
    failed — the error feeds the repair nudge.
    """
    blob = extract_json(raw)
    if blob is None:
        return None, "No JSON object found in the response."
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return None, f"Malformed JSON: {exc.msg} (at pos {exc.pos})."
    if not isinstance(data, dict):
        return None, "Top-level JSON value must be an object."
    return _coerce(data)


def _coerce(data: dict) -> tuple[ReflectionResult | None, str]:
    """Validate/repair the parsed fields per RFC-0002 §7.3.

    ``decision`` is required and must be a known verdict (else a parse failure →
    repair → acceptance bias). ``reason`` and ``confidence`` are repaired in place
    (truncated / coerced / clamped) rather than rejected.
    """
    raw_decision = data.get("decision")
    if not isinstance(raw_decision, str):
        return None, "Missing or non-string 'decision'."
    try:
        decision = ReflectionDecision(raw_decision.strip().lower())
    except ValueError:
        return (
            None,
            f"Unknown decision {raw_decision!r}; "
            "expected one of accept/retry/replan/abort.",
        )

    raw_reason = data.get("reason", "")
    reason = raw_reason.strip() if isinstance(raw_reason, str) else ""
    if len(reason) > _MAX_REASON_CHARS:
        reason = reason[:_MAX_REASON_CHARS].rstrip() + "…"

    confidence = _coerce_confidence(data.get("confidence"))

    try:
        result = ReflectionResult(
            decision=decision,
            reason=reason,
            confidence=confidence,
            source=ReflectionSource.LLM,
            reflection_version=REFLECTION_PROMPT_VERSION,
        )
    except ValidationError as exc:  # defensive: coercion should prevent this
        return None, f"Schema validation failed: {exc}"
    return result, ""


def _coerce_confidence(value: object) -> float:
    """Coerce to float and clamp to ``[0.0, 1.0]``; default on missing/garbage."""
    if isinstance(value, bool):
        # bool is an int subclass; a JSON boolean is not a confidence.
        return _DEFAULT_CONFIDENCE
    if isinstance(value, int | float):
        num = float(value)
    elif isinstance(value, str):
        try:
            num = float(value.strip())
        except ValueError:
            return _DEFAULT_CONFIDENCE
    else:
        return _DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, num))
