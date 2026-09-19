"""Tolerant JSON extraction for model output (design doc §1.2b).

Local 7B models wrap JSON in prose or code fences and occasionally emit trailing
commentary, so both the action envelope (executor) and the planner parse output
the same tolerant way: pull the most likely JSON object out of a noisy response,
then let the caller validate it against its own schema. Keeping this in one place
means there is a single parser with two callers rather than duplicated logic.
"""

from __future__ import annotations

import re

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_json(text: str) -> str | None:
    """Pull the most likely JSON object out of a model response.

    Prefers a fenced ```json block; otherwise returns the first brace-balanced
    ``{...}`` substring. Returns ``None`` if no object-like span is present.
    """
    fenced = _FENCE_RE.search(text)
    if fenced:
        return fenced.group(1)
    return first_balanced_object(text)


def first_balanced_object(text: str) -> str | None:
    """Return the first brace-balanced ``{...}`` substring, respecting strings."""
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    escaped = False
    for i in range(start, len(text)):
        ch = text[i]
        if in_str:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return None
