# ADR-0008 — Flat action envelope + graceful degradation on unparseable turns

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
The executor's ReAct loop requires the model to choose, each turn, between
calling a tool and finishing (design §1.2b). Small local models (the target,
`qwen2.5:7b-instruct`) are erratic JSON emitters: they wrap objects in prose or
code fences, and sometimes emit malformed or mis-shaped JSON. Two questions
followed: what shape should the envelope be, and what happens when the model
cannot produce it even after we ask again?

## Decision
1. **Flat envelope.** One JSON object per turn:
   `{thought, action: "tool_call"|"finish", tool?, arguments?, answer?}`.
   A flat schema (not a nested/discriminated union) is markedly more reliable for
   7B models (design §1.1). Parsing is tolerant — it extracts a fenced or
   prose-embedded object and brace-balances while respecting strings.
2. **Bounded repair loop.** On a parse/shape failure, re-prompt with the error
   (≤ `ATLAS_AGENT_REPAIR_ATTEMPTS`, default 2) before giving up on the turn.
3. **Graceful degradation.** If the turn is still unparseable after repair, accept
   the model's **first** response as a direct `finish` answer rather than failing
   the run. The first attempt holds the model's genuine content; later repair
   turns only chase JSON format.

## Consequences
- The `echo` provider (which cannot emit JSON) still yields a usable run, so dev
  and CI work without Ollama, and M1's streaming-answer behavior is preserved.
- Iteration-budget exhaustion (the model loops on tool calls and never finishes)
  remains a real failure → `run.failed` (`ExecutorError`); degradation applies
  only to *format* failures, not *non-termination*.
- Trade-off: a degraded answer may be low quality. That is acceptable at M2
  (single task, no judge); the **Reflector (M4)** will grade answers against
  success criteria and trigger retries, which is the correct place to catch this.

_Realized in code at M2 (`agent/envelope.py`, `agent/executor.py`)._
