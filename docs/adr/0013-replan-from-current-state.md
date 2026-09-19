# ADR-0013 — Replan from current state with immutable completed tasks

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
When the reflector judges that the *plan* (not the current task) is wrong for the
goal, the run must change course. ADR-0003 already chose **planner re-invocation over
a separate replanner module**. M4 makes that concrete: how does a replan interact with
work already done, task ordering, and the event ledger?

## Decision
A `replan` verdict (or retry-exhaustion with a replan remaining) invokes
**`Planner.replan(goal, completed, reason)`** — the same parse/repair/normalize
machinery as `plan`, prompted to build on completed work rather than restart it.
Rules:

- **Completed tasks are immutable.** Their outputs carry forward as context; they are
  never re-run or rewritten.
- **Remaining tasks are dropped** — the triggering task and everything after it are
  marked `SKIPPED` (their ids become the `plan.replanned` payload's
  `dropped_task_ids`).
- **Indices stay monotonic per run.** The new generation appends at
  `index > max(existing index)`, preserving `UNIQUE(run_id, index)` and the
  `{run_id}:{index}` id scheme — no row is renumbered.
- **Generations are tracked.** Each replanned task records `replan_generation` and
  `parent_generation` (the generation it descended from; `null` for the original
  plan, Amendment 9), mirrored in the `plan.replanned` event so the History/eval views
  can reconstruct the whole plan-evolution tree.
- **Bounded** to `agent_max_replans` (default 1) per run; exhaustion escalates to a
  graceful abort (ADR-0014).

## Consequences
- Reuses the planner and its normalization (non-empty, ordered, capped) — a replan
  can't corrupt the task list or reuse an index.
- The ledger fully explains the run: `task.reflected{replan}` → `task.skipped×` →
  `plan.replanned` → the new generation's `task.started`. Replay is purely
  event-derived.
- Cancellation and budgets wrap the replan (checked before/after `Planner.replan`), so
  it inherits correct stop behavior.
- Trade-off: dropping the triggering task discards a possibly-usable partial output.
  Simplicity and a clean generation boundary win over salvaging it.
