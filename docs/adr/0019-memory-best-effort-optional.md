# ADR-0019 — Memory is best-effort and disabled by default

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
Memory adds a new subsystem that reads and writes on the run's critical path
(recall before planning) and after it (write at finalization). A bug or outage in
that subsystem — an FTS error, a distillation failure, a store write error — must
never degrade the core agent, which already works without memory (M4). We also want
memory to be introducible with zero risk to existing behavior and to respect the
privacy/prompt-injection concerns of storing and re-injecting text.

## Decision
Memory is **optional and best-effort**:

- **Disabled by default** (`ATLAS_MEMORY_ENABLED=false`). When off, no memory object
  is constructed, no recall/write/event/model-call happens, and the planner prompt
  is byte-identical to M4 (invariant I-15). The `/memories` API still exists and
  simply returns an empty store.
- **Best-effort** (invariant I-14): every `MemoryService` operation catches and logs
  its own failures; recall degrades to no lessons, write degrades to heuristic or is
  skipped. Nothing memory-related can change a run's status, answer, or non-`memory.*`
  events. Writes happen only after the answer is delivered, and never on budget
  exhaustion or cancellation.
- **Safe by construction**: only distilled text is stored (ADR-0016), lessons are
  injected as untrusted, length-bounded hints (ADR-0018), the store is bounded by
  prune, and `DELETE /memories/{id}` lets a user forget.

## Consequences
- Adopting memory is a config flag with no behavioral risk to M1–M4; parity is
  test-enforced.
- The agent is strictly no worse than M4 under any memory failure mode.
- Prompt-injection and privacy exposure are contained (distilled, untrusted,
  bounded, purgeable, local-only).
- Trade-off: because memory is off by default, its planning benefit is opt-in and
  won't appear until an operator enables it and related runs accrue — an acceptable
  price for zero-risk introduction.
