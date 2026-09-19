# Architecture Decision Records

Short, dated records of significant decisions and their trade-offs. Several are
deliberate **reversals** of an earlier V1 design (`../../ATLAS-Design-Review-V2.md`),
kept because documented reversals demonstrate engineering judgment.

| ADR | Title | Status |
|---|---|---|
| [0001](0001-record-architecture-decisions.md) | Record architecture decisions | Accepted |
| [0002](0002-list-over-dag.md) | Ordered task list over a DAG planner | Accepted |
| [0003](0003-planner-reinvocation-over-replanner.md) | Planner re-invocation over a replanner module | Accepted |
| [0004](0004-fts5-over-faiss.md) | SQLite FTS5 episodic memory over FAISS in v1 | Accepted |
| [0005](0005-emit-over-event-bus.md) | `emit()` + events table over an event bus | Accepted |
| [0006](0006-python314-wheels.md) | Wheel-only installs / plain uvicorn on Python 3.14 | Accepted |
| [0007](0007-defer-speculative-infra.md) | Defer speculative infrastructure | Accepted |
| [0008](0008-envelope-and-graceful-degradation.md) | Flat action envelope + graceful degradation | Accepted |
| [0009](0009-event-sourced-plan-checklist.md) | Plan checklist is event-sourced | Accepted |
| [0010](0010-synthesis-as-distinct-call.md) | Synthesis is a distinct LLM call | Accepted |
| [0011](0011-reflector-acceptance-bias.md) | Reflector verdict + acceptance bias | Accepted |
| [0012](0012-central-budget-enforcement.md) | Central budget enforcement via wrappers | Accepted |
| [0013](0013-replan-from-current-state.md) | Replan from current state, completed tasks immutable | Accepted |
| [0014](0014-graceful-abort-partial-answer.md) | Graceful abort yields a partial answer | Accepted |
| [0015](0015-light-sqlite-migrations.md) | Light, idempotent SQLite column migrations | Accepted |
| [0016](0016-episodic-memory-record.md) | Episodic memory record & distillation | Accepted |
| [0017](0017-fts5-external-content-sync.md) | FTS5 external-content sync, LIKE fallback | Accepted |
| [0018](0018-recall-at-plan-time.md) | Recall at plan time only, recorded in the ledger | Accepted |
| [0019](0019-memory-best-effort-optional.md) | Memory is best-effort and disabled by default | Accepted |
| [0020](0020-deterministic-benchmark-harness.md) | Deterministic benchmark & profiling harness | Accepted |
| [0021](0021-crash-recovery-reconcile-not-resume.md) | Crash recovery: reconcile-to-terminal, not resume | Accepted |
| [0022](0022-passive-dependency-free-observability.md) | Passive, dependency-free observability | Accepted |
| [0023](0023-replay-verification-as-a-test.md) | Replay verification as a first-class test | Accepted |
| [0024](0024-single-writer-concurrency-model.md) | Single-writer concurrency model, documented and hardened | Accepted |

Template: Context → Decision → Consequences.
