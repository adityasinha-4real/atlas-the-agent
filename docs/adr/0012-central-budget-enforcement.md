# ADR-0012 — Central budget enforcement via gateway/registry wrappers

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
Self-correction adds loops — retry, replan, a reflection call per attempt — on top
of the per-task ReAct loop. A pathological run (a task the model keeps rejecting, a
tool it keeps re-calling) must be **bounded so every run terminates** (invariant
I-10). Threading "have we spent too much?" checks through the planner, executor,
reflector, and synthesizer call sites would be error-prone and easy to forget at the
one site that matters.

## Decision
A single per-run **`RunBudget`** counts model calls, tool calls, retries/task, and
replans/run. `model_calls` and `tool_calls` are **hard caps**; `planner`/
`reflection`/`synthesis` are separate attribution counters (Amendment 7). Enforcement
is **central**, via thin wrappers created once per run:

- **`BudgetedGateway(inner, budget, category)`** — charges one model call *before*
  delegating; each component gets a wrapper bound to its category.
- **`BudgetedToolRegistry(inner, budget)`** — charges one tool call *before*
  delegating, **outside** the real registry's try/except.

Charging past a hard cap raises **`BudgetExceeded`** — deliberately a plain
`Exception`, not `LLMError` or a tool error, so it is **not** double-handled: it
propagates out of the executor (never trapped as a tool observation, invariant I-9)
straight to the `RunManager`, which finalizes the run `FAILED` with **no** partial
answer (out of resources — distinct from the graceful abort of ADR-0014). Retries and
replans are checked explicitly in the manager (control-flow, not I/O). Only the
`charge_*` methods mutate — a single write path, mirroring single-writer discipline.

## Consequences
- One counting site per resource; no call site can forget to check. Adding a future
  component (e.g. a critic) just wraps its gateway with the right category.
- Wrappers are **transparent** under default caps (60 model / 40 tool): a normal run
  never trips them, so reflection-disabled runs stay byte-for-byte M3 (I-13).
- `BudgetExceeded`'s non-`LLMError` type is load-bearing — it is what lets a budget
  hit escape the executor's tool-error trap. Documented so it is never "helpfully"
  reclassified.
- Trade-off: a run that would have finished in one more call fails at the cap. The
  caps are generous by design; they bound pathology, not normal work.
