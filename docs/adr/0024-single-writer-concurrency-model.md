# ADR-0024 — Single-writer concurrency model, documented and hardened

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
ATLAS runs many concurrent runs in one process over a single SQLite file. M6's
concurrency review (RFC-0004 §11) needs to state the model explicitly and harden
within it, rather than lift it — introducing a broker or Postgres now would be
premature (ADR-0007). Under load, WS-driven read bursts and a finalizing run can
contend for the same database.

## Decision
Document and hold the **single-writer** model:

- **One logical SQLite writer, many readers**, in WAL mode. Writes are serialized;
  WAL lets reads proceed concurrently without blocking the writer.
- **One asyncio task orchestrates each run**; cross-run isolation is by `run_id`.
  `emit()` serializes `seq` allocation per the append seam so ledger ordering stays
  gapless and monotonic (I-24).
- **The in-process `EventHub` is single-process** (ADR-0005) — a known ceiling, not
  a bug.

Hardening **within** the ceiling (no scope expansion):
- `PRAGMA busy_timeout` so a transient write lock retries instead of erroring under
  read bursts.
- Bounded work: no long-held write transaction spans a model/LLM call (transactions
  never wrap network I/O).
- The finalize/backfill read race is closed at the test layer by waiting on the
  terminal *event* (not the row status); the runtime-level reconciliation of an
  interrupted finalize is handled by recovery (ADR-0021).

## Consequences
- The concurrency contract is explicit and testable; parallel-run and WS-fan-out
  tests exercise it.
- SQLite write serialization is a throughput ceiling but a correctness *floor* — no
  torn writes, no lost events — appropriate for a single-node agent runtime.
- Trade-off: horizontal scale needs a real event bus and a shared DB (Postgres);
  both sit behind existing seams (`emit()`/`EventHub`, the repositories) so the move
  is bounded when it is justified — deferred until then.
