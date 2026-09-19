"""The ReAct action envelope and tolerant JSON parsing (design doc §1.2b).

Every executor turn, the model must emit a single JSON object choosing one
action: call a tool, or finish with an answer. Local 7B models wrap JSON in prose
or code fences and occasionally emit malformed JSON, so parsing is deliberately
tolerant and pairs with a repair loop in the executor (repair ≤ N, design §3).

``AgentAction`` is a *flat* schema on purpose — flat JSON is markedly more
reliable for small models than nested/discriminated shapes (design §1.1).
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError, model_validator

from atlas.agent.jsonio import extract_json


class ActionType:
    TOOL_CALL = "tool_call"
    FINISH = "finish"


class AgentAction(BaseModel):
    """One decision from the executor's ReAct loop."""

    thought: str = ""
    action: Literal["tool_call", "finish"]
    tool: str | None = None
    arguments: dict[str, Any] = Field(default_factory=dict)
    answer: str | None = None

    model_config = {"extra": "ignore"}

    @model_validator(mode="after")
    def _check_shape(self) -> AgentAction:
        if self.action == ActionType.TOOL_CALL:
            if not self.tool:
                raise ValueError("action 'tool_call' requires a non-empty 'tool'.")
        elif self.action == ActionType.FINISH:
            if self.answer is None:
                raise ValueError("action 'finish' requires an 'answer'.")
        return self

    @property
    def is_finish(self) -> bool:
        return self.action == ActionType.FINISH


class ParseResult(BaseModel):
    """Outcome of parsing a raw model turn into an ``AgentAction``."""

    action: AgentAction | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.action is not None


def parse_action(raw: str) -> ParseResult:
    """Parse a raw model completion into an ``AgentAction``, tolerantly."""
    blob = extract_json(raw)
    if blob is None:
        return ParseResult(error="No JSON object found in the response.")
    try:
        data = json.loads(blob)
    except json.JSONDecodeError as exc:
        return ParseResult(error=f"Malformed JSON: {exc.msg} (at pos {exc.pos}).")
    if not isinstance(data, dict):
        return ParseResult(error="Top-level JSON value must be an object.")
    try:
        return ParseResult(action=AgentAction.model_validate(data))
    except ValidationError as exc:
        first = exc.errors()[0]
        loc = ".".join(str(p) for p in first["loc"]) or "(root)"
        return ParseResult(error=f"Invalid action shape — {loc}: {first['msg']}.")
