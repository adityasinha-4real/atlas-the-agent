# ADR-0023 — Replay verification as a first-class test

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
ATLAS's central design claim is that the event ledger is authoritative: the run
page, the plan checklist, the task views, and the final answer are all *derived*
from events, so a run can be replayed and rebuilt from its ledger (design doc §1.7,
ADR-0009). Through M5 this was a claim backed by convention, not a checked
invariant. Crash recovery (ADR-0021) now *depends* on it — the reconciler decides an
interrupted run's fate by folding its ledger — so the claim must become verifiable.

## Decision
Introduce a **pure reducer**, `atlas/recovery/replay.py::fold_events`, that folds an
ordered event stream into derived state (run status, answer, error, per-index task
checklist) with no I/O and no dependence on the row projections. It accepts both
`Event` models and plain `{"type", "payload"}` dicts, so live events and committed
JSON **golden ledgers** fold identically.

Replay correctness is then enforced as a **first-class test** (RFC-0004 §15,
invariant I-24):

- **Round-trip:** run an echo run, fold its persisted events, and assert the fold
  equals the persisted `RunView`/`TaskView`s. Any code path that mutates derived
  state outside the ledger breaks this test.
- **Golden ledgers:** committed event fixtures (`tests/golden_ledgers/*.json`)
  covering shapes the echo provider does not produce — partial abort, cancellation —
  fold to asserted derived state, pinning the reducer's branches.

The reducer is shared by the reconciler and the tests, so "the system can rebuild
everything from events" is exercised on every CI run.

## Consequences
- Ledger authority (I-24) is a guarded invariant, not a claim; drift between the
  ledger and derived state is caught immediately.
- The reconciler reuses the reducer, so recovery decisions and UI/API projections
  interpret events the same way — no second, divergent interpreter.
- Golden ledgers give cheap, deterministic coverage of failure/recovery shapes
  without a live model.
- Trade-off: the backend reducer and the frontend's event-sourced reducer are two
  implementations of the same fold; a future step (RFC-0004 §15) shares the golden
  fixtures across both to prevent drift. For M6 the backend reducer is the checked
  reference.
