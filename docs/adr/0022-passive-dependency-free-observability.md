# ADR-0022 — Passive, dependency-free observability

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
Operating ATLAS needs visibility — request/run counts, latencies, dependency health,
correlated logs — but M6 is hardening-only and must preserve byte-for-byte M5
behavior by default. A heavy observability stack (Prometheus client, OpenTelemetry,
an APM agent) adds dependencies, a metrics endpoint with unbounded label cardinality
is a memory leak, and any recording path that can raise into a run would violate the
best-effort principle we hold for memory (ADR-0019).

## Decision
Observability is **passive, opt-in, dependency-free** (RFC-0004 §28-30):

- **Metrics**: a tiny in-process registry (`atlas/obs/metrics.py`) with `Counter`,
  `Gauge`, `Histogram`, rendered in the Prometheus text exposition format at
  `/metrics` behind `ATLAS_METRICS_ENABLED` (default off → 404). **Cardinality is
  fixed**: labels are only bounded enums (event type, version) — never per-run ids —
  so memory is bounded (invariant I-28). Every run/task/tool/memory metric derives
  from `atlas_events_total{type}`. Latency histograms use fixed buckets
  (`0.5ms … 10s`, roughly powers-of-ten with 2.5/5 midpoints) chosen to straddle the
  sub-millisecond framework path and the seconds-scale model path.
- **Logging**: an opt-in `ATLAS_LOG_FORMAT=json` structured formatter with `run_id`/
  `task_id` correlation via a context var; text remains the unchanged default.
- **Health vs readiness**: `/health` (liveness + version) and a new `/ready`
  (DB + FTS5 + provider probe, 200/503).

All recording is a **no-op fast path when disabled** and exception-isolated when
enabled — it never mutates run state and never raises into a run (invariant I-25).
Enabling it changes no event, order, or output (I-23), which is test-enforced.

## Consequences
- Zero new dependencies; the metrics surface is a few hundred lines and bounded.
- Default behavior is byte-for-byte M5; observability is a pure operational add-on.
- Trade-off: an in-process registry does not aggregate across instances (matches the
  single-process `EventHub`, ADR-0024) and offers no push/remote-write. If ATLAS
  ever scales horizontally, swapping in a real client is a bounded change behind the
  same registry seam. A future `/live` vs `/ready` split and OpenTelemetry tracing
  are named, deferred upgrades.
