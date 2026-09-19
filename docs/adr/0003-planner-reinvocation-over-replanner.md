# ADR-0003 — Planner re-invocation over a replanner module

- **Status:** Accepted (reverses V1)
- **Date:** 2026-07-09

## Context
V1 specified a dedicated replanner that patched the plan DAG via
replace/insert/drop operations — a whole module and patch-op vocabulary for what
is fundamentally one prompt. Graph surgery on a weak model is error-prone and
hard to demo.

## Decision
Replanning is **re-invoking the planner** with extra context (original goal,
completed tasks + outputs, failure critique) to produce a revised list for the
remaining work. Rate-limited to **once per run** to prevent oscillation on a weak
model.

## Consequences
- Same capability and demo ("the agent revised its plan") at a fraction of the
  code and with ~zero new schema.
- Bounds worst-case cost and avoids replan storms.
- Graph patching remains a future option if regeneration ever measurably fails.

_Realized in code at M4._
