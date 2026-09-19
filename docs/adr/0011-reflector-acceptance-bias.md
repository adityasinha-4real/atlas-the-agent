# ADR-0011 — Reflector verdict + acceptance bias

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
M4 adds a **reflector** that grades each task attempt and picks the next action:
`accept | retry | replan | abort`. The judge is the same small local model
(`qwen2.5:7b-instruct`) that produced the work. Small models are unreliable
self-critics in a specific direction: they **over-criticize** and hedge, and their
free-form output is often malformed. If an unparseable or low-confidence verdict
defaulted to *reject*, a run would thrash — retrying and replanning good-enough work
until it exhausted its budget, never terminating with an answer.

## Decision
Make the reflector a **stateless value-producing component** (`Reflector.reflect →
ReflectionResult`) with a deliberate **acceptance bias**:

- A cheap **deterministic pre-check** handles the unambiguous cases with no model
  call: empty or `ERROR:` output → `retry` (confidence 0.9).
- Otherwise ask the model for a JSON verdict, tolerant-parsed with a bounded repair
  loop (shared `jsonio`, mirroring the planner).
- When no usable verdict can be produced — unparseable after repair, or the provider
  failed — fall back to **`accept`** (a bounded, mediocre answer beats an unbounded
  self-doubt loop). This is kin to ADR-0008's graceful degradation.
- An optional confidence floor (`agent_reflection_min_confidence`, default 0 = off)
  downgrades an under-confident `retry`/`replan` to `accept`.

The reflector references **no run/task state** (Amendment 10 / invariant I-7), so it
is trivially testable and reusable. The persisted verdict records a
`reflection_version` so historical reflections stay interpretable after a prompt
revision (Amendment 6).

_The reserved `EventType.REFLECTION`/`REPLAN` placeholders are renamed to the
`subject.verb` convention (`task.reflected`, `plan.replanned`) as part of this
change — small enough to record here rather than in its own ADR._

## Consequences
- Runs terminate: the failure modes of a weak judge bias toward finishing, not
  looping. Budgets (ADR-0012) are the hard backstop behind the soft bias.
- Reflection is a pure function of `(goal, task, output)` — deterministic tests with
  the FakeLLM, no I/O, no mocking of persistence.
- Trade-off: the agent sometimes accepts a mediocre answer it *could* have improved.
  That is the intended bias; the eval harness (M5) will measure how often it matters.
