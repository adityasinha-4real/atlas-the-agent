# ADR-0018 — Recall at plan time only, recorded in the ledger

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
Recalled lessons could be injected at several points: planning, execution
(per-task context), or reflection. Each extra injection point adds prompt size,
coupling, and prompt-injection surface, and complicates the "disabled = M4"
guarantee. Recall also introduces *external* state (the cross-run store) into a
run, which threatens the "a run is reconstructable from its ledger" invariant (I-1).

## Decision
In v1, recall happens **once, at plan time only**: `RunManager._plan` calls
`MemoryService.recall(goal)` before `Planner.plan`, and the ranked/budgeted lessons
are injected into the planner prompt as an explicitly-**untrusted** hints block. The
same recalled block is **threaded to any replan** (no second query). Execution and
reflection prompts are untouched. To keep the run auditable, the `memory.recalled`
event stores the **exact injected text** (plus ids/count), so a replay reproduces
what the planner saw even though the `memories` table itself is cross-run state
outside the run's ledger.

Ranking is deterministic: an **absolute** term-overlap relevance (so an unrelated
goal scores low and is gated out, rather than being normalized up), plus salience,
recency, and outcome bias; then a min-score gate, top-k, character budget, and a
redundancy filter.

## Consequences
- One clean integration point; execution/reflection behavior is unchanged, so
  M4's reflection parity (I-13) and the "disabled = M4" guarantee (I-15) hold.
- Replan reuse means lessons persist across generations without a second query and
  without diverging from the disabled path.
- The ledger fully explains a run's planning inputs (I-19); the store is
  explicitly out of per-run replay scope (the deliberate boundary of I-1).
- Trade-off: lessons cannot influence a specific task's execution or the
  reflector's verdict yet. Both are natural, additive future extensions and were
  left out to bound M5.
