# ADR-0002 — Ordered task list over a DAG planner

- **Status:** Accepted (reverses V1)
- **Date:** 2026-07-09

## Context
V1 planned goals as a DAG. A DAG buys parallelism and complex dependencies —
neither of which a single-run, single-worker, laptop-CPU system exploits. It
costs topological scheduling, cycle detection, a harder JSON schema for a 7B
model to emit correctly, a harder timeline UI, and a much harder replanner.

## Decision
The planner emits an **ordered task list**: `[{id, description, success_criteria,
suggested_tool}]`, executed sequentially, capped at 5 tasks. A list is a
degenerate DAG, so introducing real dependencies later is additive.

## Consequences
- Roughly halves planner + runtime + UI complexity.
- Higher JSON reliability on small local models (flat, simple schema).
- A checklist UI is more legible in a 5-minute skim than a graph (design §4).
- If genuine parallelism is ever needed, migrate the list to a DAG behind the
  same planner contract — no data migration of past runs required.

_Realized in code from M3 (planner + `tasks` table)._
