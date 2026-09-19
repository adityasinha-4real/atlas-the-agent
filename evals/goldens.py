"""Golden evals: deterministic goals with expected properties (RFC-0004 §21).

Each golden is a goal plus the exact ``ScriptedGateway`` responses that drive the
run and the properties the outcome must satisfy. Scripts are authored against the
runtime's call order: planner -> per-task executor turns -> (reflector when
reflection is on) -> synthesizer (only for multi-output plans). Extra trailing
responses are harmless — the gateway only errors when *exhausted* — so synthesis
responses are provided defensively.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def plan(*tasks: dict) -> str:
    return json.dumps({"tasks": list(tasks)})


def finish(answer: str) -> str:
    return json.dumps({"thought": "done", "action": "finish", "answer": answer})


def tool_call(tool: str, **arguments: object) -> str:
    return json.dumps(
        {"thought": "use tool", "action": "tool_call", "tool": tool,
         "arguments": arguments}
    )


def reflect(decision: str, reason: str = "", confidence: float = 0.85) -> str:
    return json.dumps(
        {"decision": decision, "reason": reason, "confidence": confidence}
    )


@dataclass
class Golden:
    """One evaluable scenario and its acceptance properties."""

    id: str
    category: str
    goal: str
    responses: list[str]
    settings: dict = field(default_factory=dict)
    expect_status: str = "done"
    answer_contains: str | None = None
    expect_events: tuple[str, ...] = ()
    min_tool_calls: int = 0
    max_model_calls: int | None = None


GOLDENS: list[Golden] = [
    Golden(
        id="single_task",
        category="single-task",
        goal="Name the capital of France.",
        responses=[
            plan({"description": "State the capital.", "suggested_tool": None}),
            finish("Paris is the capital of France."),
        ],
        answer_contains="Paris",
        expect_events=("plan.created", "task.completed", "run.completed"),
        max_model_calls=3,
    ),
    Golden(
        id="multi_task",
        category="multi-task",
        goal="Compare the areas of two lakes and summarize.",
        responses=[
            plan(
                {"description": "Find area of lake A."},
                {"description": "Find area of lake B."},
            ),
            finish("Lake A is 500 sq km."),
            finish("Lake B is 300 sq km."),
            "Synthesized: Lake A (500) is larger than Lake B (300).",
        ],
        answer_contains="Synthesized",
        expect_events=("plan.created", "answer.completed", "run.completed"),
        max_model_calls=5,
    ),
    Golden(
        id="tool_use",
        category="tool-use",
        goal="Compute 21 times 2.",
        responses=[
            plan({"description": "Calculate the product.",
                  "suggested_tool": "calculator"}),
            tool_call("calculator", expression="21*2"),
            finish("The result is 42."),
        ],
        answer_contains="42",
        expect_events=("tool.call", "tool.result", "run.completed"),
        min_tool_calls=1,
        max_model_calls=4,
    ),
    Golden(
        id="retry_then_accept",
        category="retry",
        goal="Write a precise one-line summary.",
        responses=[
            plan({"description": "Summarize."}),
            finish("vague summary"),
            reflect("retry", "missing the key figure"),
            finish("Revenue grew 12% to $4.2M in Q3."),
            reflect("accept", "precise now"),
        ],
        settings={"agent_enable_reflection": True},
        answer_contains="12%",
        expect_events=("task.retrying", "task.reflected", "run.completed"),
        max_model_calls=6,
    ),
    Golden(
        id="replan",
        category="replan",
        goal="Draft an approach, then correct course.",
        responses=[
            plan({"description": "t0"}, {"description": "t1"}),
            finish("t0 done"),
            reflect("accept"),
            finish("t1 attempt"),
            reflect("replan", "wrong approach for the rest"),
            plan({"description": "t2"}),
            finish("t2 done"),
            reflect("accept"),
            "Synthesized final over t0 and t2.",
        ],
        settings={"agent_enable_reflection": True},
        expect_events=("plan.replanned", "run.completed"),
        max_model_calls=10,
    ),
    Golden(
        id="partial_abort",
        category="partial",
        goal="Complete a two-step task where step two is impossible.",
        responses=[
            plan({"description": "t0"}, {"description": "t1"}),
            finish("t0 done"),
            reflect("accept"),
            finish("t1 attempt"),
            reflect("abort", "cannot proceed with available tools"),
        ],
        settings={"agent_enable_reflection": True},
        expect_status="failed",
        answer_contains="partial",
        expect_events=("answer.completed", "run.failed"),
        max_model_calls=6,
    ),
    Golden(
        id="retry_exhaustion_fails",
        category="failure",
        goal="Attempt an impossible task with no replan budget.",
        responses=[
            plan({"description": "impossible task"}),
            finish("attempt 1"),
            reflect("retry", "not good enough"),
            finish("attempt 2"),
            reflect("retry", "still not good"),
        ],
        settings={"agent_enable_reflection": True, "agent_max_retries": 1,
                  "agent_max_replans": 0},
        expect_status="failed",
        expect_events=("task.failed", "run.failed"),
        max_model_calls=6,
    ),
]
