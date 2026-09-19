# ADR-0007 — Defer speculative infrastructure

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
It is tempting to pre-build for scale: a message broker, a model router, a DAG
scheduler, worker queues, multi-tenant auth. At the project's actual scale
(single user, single run at a time, local model) each is speculative generality
that adds cost and risk without demo or interview value.

## Decision
Do **not** build those now. Instead introduce cheap **seams** today so each
becomes a bounded refactor later:

| Future need | Seam in place now |
|---|---|
| Postgres | repository pattern + SQLAlchemy |
| Worker queue (arq/celery) | run-as-persisted-ledger (resumable) |
| Redis/NATS bus | stable `emit()` signature |
| Vector memory | `MemoryStore` interface (M5) |
| Model router | `model` param on `LLMGateway` |

## Consequences
- The codebase stays small and legible; risk is confronted where it actually
  lives (small-model reliability), not in imagined future load.
- Each deferred capability has a documented, low-cost upgrade path.
- This decision is itself a strong interview answer about right-sizing.
