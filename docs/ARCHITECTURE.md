# ATLAS Architecture

This document reflects the **implemented** system as of Milestone M6 and how it
extends toward the full V2 design (`../ATLAS-Design-Review-V2.md`). Where a
component is a stub or seam awaiting a later milestone, it is marked. M6 added no
new agent behavior — its subsystems (benchmarks, observability, recovery, evals)
are off/passive by default, so a default install is byte-for-byte M5 (I-23).

## 1. System overview

```
┌─────────── FRONTEND (Next.js · TS · Tailwind) ───────────┐
│  Run page: goal · plan · feed · answer  ·  Memory page    │
│  [M5: History + replay pending]                           │
└───────────────────────┬───────────────────────────────────┘
              REST + WebSocket (seq-numbered, replayable)
┌───────────────────────▼───────────────────────────────────┐
│                     FASTAPI BACKEND                        │
│  Run FSM: CREATED → PLANNING → RUNNING → DONE              │
│           (→ FAILED, → CANCELLED, RUNNING ⇄ PAUSED)        │
│                                                            │
│  RunManager: PLANNING → per-task RUNNING → SYNTHESIS       │
│      │  Planner ─┐                                          │
│      │  Context Builder → Executor (ReAct ≤5) ─┐           │
│      │  Synthesizer ─────────────────────────► LLMGateway  │
│      │              │         (ollama | echo | scripted)   │
│      │              └──► Tool Registry: calculator ·        │
│      │                   web_search · web_fetch · file_ops  │
│      └──► emit(event) → events table (seq) + WS hub queue  │
│  Repositories (single-writer) over SQLite (WAL)           │
│  Reflector (M4)  ·  MemoryStore FTS5 (M5, recall+write)    │
├────────────────────────────────────────────────────────────┤
│  SQLite (WAL): runs · events · tasks · task_attempts · memories│
└────────────────────────────────────────────────────────────┘
                    Ollama · qwen2.5:7b-instruct
```

## 2. Seams (built now, per design §7)

These abstractions exist from M1 so later capability is a bounded change, not a
rewrite:

| Seam | Location | Enables later |
|---|---|---|
| `LLMGateway` | `atlas/llm/gateway.py` | model router, alt providers |
| `emit(event)` | `atlas/events/emit.py` | Redis/NATS bus (signature stable) |
| Repository pattern | `atlas/persistence/repositories.py` | SQLite → Postgres |
| Run-as-persisted-ledger | `runs` + `events` tables | worker-queue runtime, crash-resume (M6) |
| Event `seq` | `EventRow.seq` | replay, WS backfill, ordering |
| Frozen contracts | `agent/schemas.py`, `events/types.py` | small-context sessions (§10) |

Explicitly **not** built yet (speculative): message broker, model router, DAG
scheduler, multi-tenant auth. See [ADR-0007](adr/0007-defer-speculative-infra.md).

## 3. Data model (M1–M5)

All four V2 tables now exist (plus M4's `task_attempts`):

**`runs`** — the ledger head / projection.
`id, goal, status, answer, error, created_at, updated_at`

**`events`** — the append-only ledger (source of truth).
`id, run_id → runs.id, seq, type, payload(JSON), ts`
with `UNIQUE(run_id, seq)` and an index on `(run_id, seq)`.

**`tasks`** (M3, +M4) — the ordered per-run task list.
`id, run_id → runs.id, index, description, success_criteria, suggested_tool,
status, output, error, attempt_count, replan_generation, parent_generation,
created_at, updated_at` with `UNIQUE(run_id, index)`; cascade-deleted with the run.
The M4 columns are added to existing databases by an idempotent
`ALTER TABLE … ADD COLUMN` guard ([ADR-0015](adr/0015-light-sqlite-migrations.md)).

**`task_attempts`** (M4) — every attempt of a task and its reflection verdict.
`attempt_id, task_id → tasks.id, run_id → runs.id, attempt_number, output, error,
reflection_{decision,reason,confidence,source,version}, created_at` with
`UNIQUE(task_id, attempt_number)`; cascade-deleted with its task/run.

**`memories`** (M5, RFC-0003) — one distilled record per finished run: `id,
run_id → runs.id (SET NULL), goal, outcome, success, summary, lessons, tools_used,
task_count, source, salience, use_count, last_recalled_at, pinned, status,
created_at, updated_at`, with `UNIQUE(run_id)` (idempotent per-run write). A
`memories_fts` FTS5 external-content index over `goal`/`summary`/`lessons` is kept
in sync by triggers and created by an idempotent migration with a `LIKE` fallback
([ADR-0017](adr/0017-fts5-external-content-sync.md)); `run_id` is `ON DELETE SET
NULL` so a lesson outlives its run. SQLite runs in **WAL** with
`synchronous=NORMAL`, `foreign_keys=ON`, `busy_timeout=5000`.

## 4. Run lifecycle (M3, self-correction M4)

```
CREATED ─► PLANNING ─► RUNNING ──► (synthesize) ─► DONE
              │  plan     │  each task via the ReAct loop
              │           │  ├─ reflect → accept | retry | replan | abort   (M4)
              │           │  └─ replan → new generation (re-enter RUNNING)
              │           │
              ├───────────┼──► FAILED   (abort/exhaustion → partial answer;
   CANCELLED ◄┴───────────┘             invalid plan; or BudgetExceeded → no partial)
```

`RunManager` sets `PLANNING`, runs the Planner, persists the `tasks`, then for
each task in order sets `RUNNING`, builds context, runs the Executor, and
persists the task's output/status; finally the Synthesizer composes the answer.

**M4 self-correction** (opt-in, `agent_enable_reflection`): each attempt's output is
judged by the **Reflector** (`accept | retry | replan | abort`). A `retry` re-runs the
task with a critique (≤2/task); a `replan` re-plans the *remaining* work into a new
generation (≤1/run, completed tasks immutable); an `abort`/exhaustion **gracefully
aborts** with a partial answer. A per-run **`RunBudget`** wraps the gateway and tool
registry and hard-caps model/tool calls — `BudgetExceeded` fails the run with no
partial. The task FSM adds `RETRYING`/`CANCELLED` and now exercises `SKIPPED`; every
finalizer leaves all tasks terminal (invariant I-4). With reflection **off**, the loop
degenerates to M3's single attempt (invariant I-13). Details:
[M4.md](milestones/M4.md), [RFC-0002](rfc/0002-m4-self-correction.md).

## 5. Event flow (the core mechanism)

1. `RunManager` calls `emitter.emit(run_id, type, payload)`.
2. `emit()` takes a process lock, allocates the next `seq` (`max(seq)+1` for the
   run), appends the row (commit), then publishes to the `EventHub`.
3. The `EventHub` fans the event out to every live WebSocket subscription for
   that run.
4. A WebSocket client connects with `?after=N`: the server **subscribes first**,
   then backfills persisted events with `seq > N`, then streams live events with
   `seq > lastBackfilled`. No gaps, no duplicates. Finished runs are fully
   replayable via backfill alone.

This single mechanism provides trace, live streaming, reconnection backfill, and
replay — see [ADR-0005](adr/0005-emit-over-event-bus.md).

## 6. LLM gateway

`LLMGateway` is the only boundary to a model. `build_gateway(settings)` is the
sole provider-selection site. `model` and `temperature` are per-call parameters
so a future router is config, not a refactor.

- **`ollama`** — streams `/api/chat`; wraps transport/protocol errors as
  `LLMError`.
- **`echo`** — deterministic fake; streams a stable answer word-by-word. Used for
  dev without Ollama and for CI. **Planner-aware** (M3): given a planner prompt it
  returns a valid single-task plan (the task is the goal), so a full plan →
  execute → answer cycle completes model-free.
- **`scripted`** (`ScriptedGateway`, test-only) — the **FakeLLM**: replays fixed
  completions so the agent loop is driven deterministically with no model.

## 7. Agent loop & tools (M2)

Each task is driven by the **Executor** — a hand-built ReAct loop
(`atlas/agent/executor.py`), the heart of the agent (design §1.2). As of M3 it is
**execution-only**: it receives a prebuilt message list from the Context Builder
and a `task_id` (for event tagging), and never constructs its own context.

1. The Context Builder supplies the prompt (system contract + tool specs + task
   framing + prior observations); the loop runs on it.
2. The model emits one **flat JSON action envelope**
   (`{thought, action: tool_call|finish, tool?, arguments?, answer?}`). Parsing is
   tolerant (fences/prose/nested braces); malformed output triggers a **repair
   loop** (≤ `agent_repair_attempts`). If still unparseable, the first response is
   accepted as the answer — graceful degradation ([ADR-0008](adr/0008-envelope-and-graceful-degradation.md)).
3. On `tool_call`: emit `tool.call`, run the tool via the registry, emit
   `tool.result`, append the observation, loop (≤ `agent_max_iterations`).
4. On `finish`: return the answer; `RunManager` streams it as `answer.token`.

**Invariants:** every tool outcome is an observation — the **`ToolRegistry`** is
the sole choke point and converts unknown-tool / invalid-args / timeout / crash
into a failed `ToolResult`, so no tool exception ever crosses the loop (§1.2c).
Tools raise `ToolError` for expected failures; the registry traps them.

**P0 tools** (`atlas/tools/`, design §5): `calculator` (safe AST eval — never
`eval`), `file_read`/`file_write` (confined to a **path jail**), `web_search`
(ddgs), `web_fetch` (HTML→text, truncated at source). `GET /tools` exposes their
specs.

## 8. Planning, context & synthesis (M3)

`RunManager` wraps the per-task executor loop with three agent components
(`atlas/agent/`), all reusing the `LLMGateway` and tolerant `jsonio` parser:

- **Planner** (`planner.py`) — one `complete()` call turns the goal into
  `{"tasks":[…]}` (≤ `agent_max_tasks`), tolerant-parsed with a repair loop
  (≤ `agent_repair_attempts`). Output is normalized: empty tasks dropped, unknown
  `suggested_tool` hints nulled against the registry, hard-capped, and an empty
  plan falls back to a single task = the goal. Invalid after repair →
  `PlannerError` → run `FAILED` during PLANNING ([ADR-0002](adr/0002-list-over-dag.md)).
- **Context Builder** (`context.py`) — turns a typed **`TaskContext`** (goal +
  current task + prior outputs) into the executor's messages: the system contract
  + tool specs, then a task-framing turn with the task's description/criteria and
  prior-task outputs truncated to `context_prior_output_chars` (summarize-at-
  source, so prompt size stays ~constant across tasks, design §1.11).
- **Synthesizer** (`synthesizer.py`) — a distinct `complete()` call composes the
  final answer from the ordered task outputs; a single-task plan short-circuits to
  that task's output (no extra call) unless `agent_force_synthesis` is set
  ([ADR-0010](adr/0010-synthesis-as-distinct-call.md)).

The plan checklist is **event-sourced**: `plan.created` + `task.*` events drive
the live UI and replay; `GET /runs/{id}/tasks` is a projection over the `tasks`
table ([ADR-0009](adr/0009-event-sourced-plan-checklist.md)).

## 8b. Episodic memory (M5, opt-in)

`atlas/memory/` adds long-term memory behind a `MemoryStore` seam (`EpisodicStore`,
SQLite FTS5) so a future semantic/FAISS tier is a drop-in
([ADR-0004](adr/0004-fts5-over-faiss.md)). `MemoryService` is the runtime façade,
constructed by `RunManager` **only when `memory_enabled`** — off by default, so
behavior is byte-for-byte M4 (invariant I-15). It is **best-effort**: no
recall/write/distillation failure can affect a run (I-14).

- **Recall** (plan time) — `RecallService` retrieves FTS5 candidates, ranks them
  (absolute term-overlap relevance + salience/recency + outcome bias), applies a
  min-score gate / top-k / char budget / redundancy filter, and injects the lessons
  into the planner prompt as untrusted hints; a replan reuses them. `memory.recalled`
  records the exact injected text so replay stays ledger-derived
  ([ADR-0018](adr/0018-recall-at-plan-time.md)).
- **Write** (finalization, after the answer) — `MemoryWriter` distills the run
  (LLM with a zero-LLM heuristic fallback) into one record, upserted per run
  (idempotent), then the store is pruned; `memory.written` is emitted. Only on
  `done`/`partial`; never on budget/cancel ([ADR-0016](adr/0016-episodic-memory-record.md),
  [ADR-0019](adr/0019-memory-best-effort-optional.md)).
- **API/UI** — `/memories` (list/search/get/delete/pin) backs the Memory page.

## 8c. Hardening seams (M6, RFC-0004)

All M6 subsystems are **off, passive, or a no-op** by default (I-23); enabling them
never changes run output and can never raise into a run.

- **Observability** (`atlas/obs/`) — a dependency-free in-process metrics registry
  (counters/gauges/histograms, Prometheus text exposition, **fixed** label
  cardinality, I-28). The `EventEmitter` records timings only when metrics are
  enabled; recording is exception-isolated (I-25). `GET /metrics` (404 when off)
  and `GET /ready` (DB/FTS/provider probe) are read-only. Opt-in JSON logging adds
  run/task correlation ids ([ADR-0022](adr/0022-passive-dependency-free-observability.md)).
- **Recovery** (`atlas/recovery/`) — at startup a **reconciler** finds runs left
  non-terminal by a crash and drives each to a consistent terminal state **derived
  solely from its ledger**, emitting an attributed recovery event; idempotent and a
  no-op on a clean DB (I-26, [ADR-0021](adr/0021-crash-recovery-reconcile-not-resume.md)).
  Recovery is reconcile-to-terminal, **not** resume.
- **Replay** — `fold_events` is the project's **canonical reducer** (ledger →
  run/answer/task state); the reconciler, API projections, and tests all agree with
  it, and `sequence_is_intact` guards ledger ordering (I-24,
  [ADR-0023](adr/0023-replay-verification-as-a-test.md)).
- **Integrity/determinism** — optional `PRAGMA quick_check` + FTS-drift check at
  startup, a `schema_meta` version row (additive, I-27), and a repeated-run
  determinism test.
- **Benchmarks/evals** — `bench/` (deterministic latency/RSS/query-plan baselines +
  CI guard, [ADR-0020](adr/0020-deterministic-benchmark-harness.md)) and `evals/`
  (golden goals → `scorecard.md`), both scripted and model-free.

## 9. Concurrency & correctness

- **Single writer:** all writes go through repositories; `emit()` serializes
  `seq` allocation with an `asyncio.Lock`, guaranteeing per-run monotonicity even
  under concurrent emits (covered by `test_emit.py`).
- **Cooperative cancellation:** a per-run `asyncio.Event` is checked during
  planning, between tasks, between executor iterations, and between streamed
  chunks, so finalization writes always run (no torn state from a hard
  `task.cancel()`).
- **Failure containment:** `RunManager._execute` converts any `PlannerError`,
  `LLMError`, `ExecutorError`, or unexpected exception into a `run.failed` event
  (a task failure additionally emits `task.failed`); nothing escapes the run.

## 10. Testing strategy

- Component tests build `Database`/`EventEmitter`/`RunManager` directly on the
  test loop; agent-logic tests run against the scripted **FakeLLM**.
- API/WebSocket tests use Starlette's `TestClient` (runs the lifespan, supports
  WS) with the `echo` provider and a temp DB.
- 307 tests, deterministic, no external services (`make test`); coverage floor
  85% (measured 92%) enforced in CI.
- **M6 test families:** `test_recovery`/`test_replay`/`test_determinism`/
  `test_integrity` (recovery + ledger equivalence), `test_obs_metrics`/
  `test_obs_logging` (passive observability + parity), `test_bench`/
  `test_storage_optimization`, `test_evals` (golden scorecard), `test_docs_parity`
  (version/config doc-lint).

## 11. Extension points by milestone

| Milestone | Adds | Touches |
|---|---|---|
| M3 ✅ | Planner (list), `tasks` table, context builder, synthesizer, checklist UI | `atlas/agent`, `persistence`, frontend |
| M4 ✅ | Reflector (pre-check + 4 verdicts), retry, replan, graceful abort, budgets, `task_attempts` | `atlas/agent`, `runtime`, `persistence`, frontend |
| M5 ✅ | `MemoryStore` (FTS5), `memories` table, recall at plan time, write on finish, `/memories` API + Memory page | `atlas/memory`, `api`, `persistence`, frontend |
| M6 ✅ | Benchmarks (`bench/`), passive observability (`atlas/obs`, `/metrics`, `/ready`, JSON logs), crash recovery + replay reducer (`atlas/recovery`), integrity/determinism, eval harness (`evals/`), CI, audits | `atlas/obs`, `atlas/recovery`, `bench`, `evals`, `.github`, docs |
| M7 | Packaging polish, demo GIF, scorecard in README | docs, ci |
