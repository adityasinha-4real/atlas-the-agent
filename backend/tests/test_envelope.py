"""Tolerant parsing of the ReAct action envelope."""

from __future__ import annotations

from atlas.agent.envelope import parse_action


def test_parses_plain_tool_call() -> None:
    result = parse_action(
        '{"thought": "compute", "action": "tool_call", '
        '"tool": "calculator", "arguments": {"expression": "2+2"}}'
    )
    assert result.ok
    assert result.action.action == "tool_call"
    assert result.action.tool == "calculator"
    assert result.action.arguments == {"expression": "2+2"}


def test_parses_finish() -> None:
    result = parse_action('{"action": "finish", "answer": "42"}')
    assert result.ok and result.action.is_finish and result.action.answer == "42"


def test_extracts_json_from_code_fence() -> None:
    raw = 'Here you go:\n```json\n{"action": "finish", "answer": "hi"}\n```\nDone.'
    result = parse_action(raw)
    assert result.ok and result.action.answer == "hi"


def test_extracts_json_embedded_in_prose() -> None:
    raw = 'Sure! {"action": "finish", "answer": "ok"} hope that helps'
    assert parse_action(raw).ok


def test_handles_braces_inside_strings() -> None:
    raw = '{"action": "finish", "answer": "use {curly} braces"}'
    result = parse_action(raw)
    assert result.ok and result.action.answer == "use {curly} braces"


def test_rejects_when_no_json() -> None:
    result = parse_action("I think the answer is 42.")
    assert not result.ok and result.error


def test_rejects_malformed_json() -> None:
    result = parse_action('{"action": "finish", "answer": }')
    assert not result.ok


def test_rejects_tool_call_without_tool() -> None:
    result = parse_action('{"action": "tool_call", "arguments": {}}')
    assert not result.ok and "tool" in result.error.lower()


def test_rejects_finish_without_answer() -> None:
    result = parse_action('{"action": "finish"}')
    assert not result.ok


def test_rejects_unknown_action() -> None:
    result = parse_action('{"action": "dance"}')
    assert not result.ok
