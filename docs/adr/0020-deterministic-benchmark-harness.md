# ADR-0020 — Deterministic benchmark & profiling harness

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
M6 optimizes for robustness and performance, which requires *measurement* — but
benchmarks that depend on a live model (Ollama) are non-deterministic, slow, and
hostile to CI. We need repeatable numbers that isolate ATLAS's own framework
overhead (serialization, DB round-trips, event fan-out, FTS recall) from model
latency, and a way to guard against regressions automatically.

## Decision
A repo-root `bench/` harness (RFC-0004 §20) that runs entirely against the **echo
provider and the persistence layer** — no Ollama, no network. Model latency is
excluded by construction, so the top of a profile is ATLAS's own cost. Scenarios
build their own temp SQLite DB, seed deterministic fixtures, discard a warm-up run,
and time a hot operation many times (p50/p95/p99). Reports are written as JSON
(trend tracking) and markdown (with captured `EXPLAIN QUERY PLAN`s) alongside the
benchmark **environment** (Python, platform, CPU) for reproducibility. `python -m
bench --ci` runs a short subset against `bench/thresholds.json` with a tolerance
band, failing only on gross regression. `python -m bench.profile` cProfiles a run.

Histogram/threshold values are chosen generously (≈8–10× observed p95) so shared
CI runners do not flap; the harness guards against pathological regressions, not
micro-noise.

## Consequences
- Optimizations are justified by before/after numbers and query-plan changes, not
  intuition (the M6 `runs_list` index is the worked example).
- CI can enforce a performance floor without a model.
- Baselines are host-dependent; the committed environment block makes cross-host
  comparison honest, and the durable signal is the **delta + query plan**, not the
  absolute milliseconds.
- Trade-off: the harness measures framework overhead, not end-to-end model latency,
  which is dominated by Ollama and out of ATLAS's control — a deliberate scoping.
