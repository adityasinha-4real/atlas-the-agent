# ADR-0009 — The plan checklist is event-sourced

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
M3 introduces a plan: a run produces an ordered list of ≤5 tasks that tick
`pending → running → done/failed` live on the Run page (design §1.7). We need one
authoritative source for that checklist that serves three surfaces at once: the
live UI, WebSocket reconnect/backfill, and History-page replay (M5). The `tasks`
table stores task rows, and the ledger already records `plan.created` and
`task.*` events. The question is which one the UI reads from.

## Decision
The live checklist is **reconstructed from the event stream** — `plan.created`
seeds the task list, and `task.started` / `task.completed` / `task.failed`
transition each task — exactly as `answer.token` reconstructs the answer
(ADR-0005). The frontend derives task state inside `useRunStream`; it never polls.

`GET /runs/{id}/tasks` exists as a **projection** for initial page load and
History replay, not as the live path. It reads the `tasks` table (the durable
task rows) but is not what drives the ticking checklist.

## Consequences
- **Replay is free.** The same `after=<seq>` backfill that powers the answer and
  event feed reconstructs the checklist — no separate replay mechanism, no
  polling loop, and no divergence between "what the ledger says" and "what the UI
  shows."
- **One write path.** Task state changes are persisted (`tasks` row) *and* emitted
  (`task.*` event) at the same point in `RunManager`; the event is the UI's truth
  and the row is the queryable projection.
- **Consistency with the design's core principle** — the event log is the source
  of truth; run and task rows are projections of it (design §1.7, ADR-0005).
- Trade-off: a UI reading only the table (bypassing events) could momentarily lag
  the ledger. We accept this because the live surface is event-driven by
  construction; the endpoint is for load/replay, where the run is already settled
  or backfilled.

_Realized in code at M3 (`lib/useRunStream.ts`, `components/PlanChecklist.tsx`,
`GET /runs/{id}/tasks`)._
