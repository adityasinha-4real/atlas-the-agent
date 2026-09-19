# ADR-0005 — `emit()` + events table over an event bus

- **Status:** Accepted (reverses V1)
- **Date:** 2026-07-09

## Context
V1 used a pub/sub event bus with three sink subscribers — an in-process
microservice pattern for one producer and a couple of consumers. It also split
run history across Step/Message/ToolCall tables.

## Decision
A single `emit(event)` function (a) appends the event to an **`events` table**
(source of truth, with a monotonic per-run `seq`) and (b) publishes to the run's
in-process queue for WebSocket delivery. Step/Message/ToolCall collapse into
typed event payloads in that one table. `emit()` keeps a stable signature so a
real bus (Redis/NATS) can replace it later without touching call sites.

## Consequences
- One mechanism yields four features: **trace, live stream, WS reconnection
  backfill, and replay** (`GET /runs/{id}/events?after=N`).
- Fewer joins; the event log is the audit trail and the replay source.
- `seq` monotonicity is enforced by serializing `emit()` (single-writer);
  covered by tests.
- Trade-off: in-process fan-out is single-node; horizontal scale is deferred to
  the bus swap (see [ADR-0007](0007-defer-speculative-infra.md)).

_Realized in code at M1._
