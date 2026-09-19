# ADR-0014 — Graceful abort yields a partial synthesized answer

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
Some runs cannot reach the goal: the reflector returns `abort` (the goal is
unachievable with the available tools), or recovery is exhausted (retries **and**
replans spent). In M3 a task failure simply failed the run. But by the time recovery
is exhausted, earlier tasks have often produced genuinely useful outputs. Throwing
them away and returning a bare error wastes work the user paid for.

## Decision
On an `abort` verdict or retry/replan exhaustion, the run **gracefully aborts**:

1. Fail the in-flight task and `SKIP` the remaining pending tasks (no task left
   non-terminal — invariant I-4).
2. If any tasks completed, run **synthesis over the completed outputs only** and
   persist the result, wrapped with a clear incomplete-run notice
   (`build_partial_answer`). Provenance is preserved — the synthesizer never sees
   skipped/aborted/pending work.
3. Finalize the run **`FAILED` *with* the partial answer** (emit `answer.completed`
   then `run.failed`). A `RunView.partial` flag (derived: `FAILED` + answer present)
   surfaces it to the API/UI.

**Budget exhaustion is the deliberate exception** (Amendment 4 / invariant I-12): a
model/tool cap hit finalizes `FAILED` with **no** synthesis — the run is out of
resources, so spending another call to compose a partial would be self-defeating. The
two paths are mutually exclusive and individually tested. Cancellation takes
precedence over both.

## Consequences
- A user gets the best available answer plus an honest "this is partial" signal,
  instead of a dead end.
- The distinction "recovery exhausted → partial" vs "budget exhausted → no partial"
  is explicit and load-bearing; both are covered by tests.
- Trade-off: a partial answer over incomplete work can mislead if the notice is
  ignored. The prominent banner and the `partial` flag mitigate this; the eval
  harness (M5) will grade partial answers separately.
