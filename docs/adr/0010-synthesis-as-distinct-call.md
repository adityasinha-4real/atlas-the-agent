# ADR-0010 — Synthesis is a distinct LLM call

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
With M3 a run executes an ordered list of tasks, each producing its own output.
The user asked one goal and expects one coherent answer. How is that final answer
formed? Two options: (a) treat the last task's output as the answer, or (b) add a
dedicated step that composes the answer from all task outputs.

For a multi-step goal — _"Compare the populations of France and Germany"_ — the
last task's output ("83M") is not the answer; the answer must combine the
outputs of every task. Taking the last output only works for trivially
single-task goals.

## Decision
Add a **Synthesizer**: a distinct LLM call over the goal and the ordered
`(task, output)` pairs that produces the final answer (design §1.11). It runs
after all tasks complete and its result is streamed as `answer.token` →
`answer.completed`, unchanged from M1/M2's answer delivery.

**Single-task short-circuit (RFC-0001 decision 1).** When a plan has exactly one
completed task, synthesis is skipped and that task's output *is* the answer — no
extra LLM call. A config flag (`agent_force_synthesis`, default off) forces the
synthesis call even for single-task plans, keeping the behavior extensible.

The synthesizer invokes **no tools** and is instructed to treat task outputs as
data, not instructions (prompt-injection mitigation for content pulled in by
`web_fetch`, design §14).

## Consequences
- Multi-step goals get a composed, self-contained answer rather than a dangling
  last-task fragment.
- Single-task goals degrade cleanly and cheaply (no wasted call), which also keeps
  the `echo` dev path fast: echo plans one task, so synthesis is skipped.
- One extra LLM call per multi-task run — acceptable, and the dominant cost is
  already the per-task executor loops (§13). Latency is masked by live task
  ticking.
- Trade-off: synthesis can misread or over-compress task outputs. Grading the
  final answer against the goal is the **Reflector's** job (M4); M3 only composes.

_Realized in code at M3 (`agent/synthesizer.py`, `agent/prompts.py`)._
