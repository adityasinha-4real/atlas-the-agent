# ADR-0021 — Crash recovery: reconcile-to-terminal, not resume

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
A run is a background asyncio task over a persisted ledger. If the process dies
mid-run (crash, kill, deploy), the run's row is left non-terminal
(`created`/`planning`/`running`) with no live task driving it. On restart these
"orphaned" rows misrepresent reality — the UI shows a run as running forever — and
violate the M4 guarantee that a terminal run leaves no task in a non-terminal state
(I-4). We need a defined, tested recovery behavior without expanding scope into a
re-entrant execution engine.

Two options: (a) **resume** the interrupted run — re-enter planning/the ReAct loop
from where it stopped; (b) **reconcile** it to a consistent terminal state derived
from the ledger, without resuming.

## Decision
M6 does **(b): reconcile-to-terminal, not resume** (RFC-0004 §13). At startup a
`Reconciler` scans for non-terminal runs (after a restart, all of them are
interrupted) and, for each:

- If the ledger already contains a terminal run event, the run actually finished
  just before the crash — the row is reconciled to match the ledger and **no event
  is invented**.
- Otherwise it was genuinely interrupted: its non-terminal tasks are marked
  `cancelled` (with attributed `task.cancelled` events), the run is finalized
  `FAILED`, and a single, clearly-attributed `run.failed` event
  (`reason: interrupted_by_shutdown`, `recovered: true`) is appended.

Recovery is a **pure function of persisted state** (invariant I-26): it reconstructs
progress by folding the ledger (ADR-0023) and appends only attributed recovery
events — it never fabricates mid-run history. It is **idempotent** (a second pass
finds only terminal runs) and **on by default**, because it is a no-op for cleanly
terminated runs, so cleanly-shutdown behavior is unchanged (I-23).

Resuming execution is explicitly **deferred** to a future milestone: it needs a
re-entrant orchestrator (safe re-planning, attempt/budget continuation, dedup of
already-emitted events) that is out of M6's hardening-only scope.

## Consequences
- No orphaned non-terminal runs or tasks survive a restart; the ledger and the row
  state always agree.
- Recovery is small, safe, and fully testable with crash-simulation fixtures; it
  cannot corrupt a healthy run.
- Trade-off: an interrupted run is reported `FAILED (interrupted_by_shutdown)` with
  its partial work preserved, **not** transparently continued. Users re-issue the
  goal. This is the honest, low-risk behavior for v0.6.0; transparent resume is a
  named future upgrade behind the same ledger-authority invariant.
