# Changelog

All notable changes to ATLAS are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/); versions track milestones.

## [Unreleased]

_Additive follow-ups on the roadmap: recall at execution/reflection time, and a
semantic/FAISS memory tier._

## [0.7.0] — 2026-09-19 · Milestone M7: Ship

Release packaging and one configuration fix. **No agent-runtime change**:
planner, executor, reflector, memory and event semantics are identical to 0.6.0,
and defaults are unchanged (I-13, I-15, I-23 hold). The History/Replay page and
the generated OpenAPI client, originally planned for M7, already shipped in
0.6.0.

### Fixed
- **Config: comma-separated list settings crashed startup.** pydantic-settings
  JSON-decodes list-typed values from the environment and `.env` *before* field
  validators run. So the documented `ATLAS_CORS_ORIGINS=http://localhost:3000`
  (in `.env.example` and `docker-compose.yml`) and
  `ATLAS_MEMORY_WRITE_OUTCOMES=done,partial` raised a `SettingsError` at startup.
  This affected a `.env` copied from `.env.example` and the Docker backend.
  `cors_origins`/`memory_write_outcomes` are now `NoDecode`, so the CSV
  validator sees the raw string; JSON-list values still work. Regression tests
  exercise the real env and `.env` sources.
- **CI workflow was invalid YAML.** The unquoted
  `--only-binary=:all: -r …` install step contains a colon-space, which made
  `.github/workflows/ci.yml` unparseable. It is now quoted.
- **`frontend/.env.local.example` was never committed.** The `.env.*` ignore
  rule matched it, although the READMEs tell users to copy it. It is now
  whitelisted and committed.

### Added
- `docker-compose.echo.yml`: a model-free override that runs backend and
  frontend with the `echo` provider and no Ollama dependency
  (`docker compose -f docker-compose.yml -f docker-compose.echo.yml up --build backend frontend`).
- **OpenAPI drift gates in CI.** The backend job re-dumps `frontend/openapi.json`
  and the frontend job re-runs `gen:api`; each fails on `git diff --exit-code`.
  This closes the v0.6.0 API-audit recommendation.
- README rewrite: an architecture diagram (Mermaid), a feature table linked to
  ADRs, the tech stack, an eval summary with its scope stated, refreshed test
  numbers, three separated quick-start paths, a CI badge, and a marked demo-GIF
  placeholder.

### Changed
- Version → **0.7.0** (backend, frontend, `/health`, OpenAPI schema).
- `backend/.dockerignore` excludes `.env` from the build context.
- Docs: `docs/TODO.md` reconciled with the repository (M7 status, deferred
  list); known-limitations and API-consistency notes updated.

### Verification
- 312 backend tests, 92% coverage; ruff clean; frontend typecheck and build
  green; `bench --ci` within thresholds; evals 8/8. Benchmark baselines were not
  re-recorded because no runtime code changed.
- Docker: images built and an echo-mode stack smoke-tested locally (`/health`,
  `/ready`, one run to `done`, UI routes 200, CORS). A clean-machine run and the
  Ollama path are still owner actions.

### Migration
- None. No schema change.

## [0.6.0] — 2026-07-10 · Milestone M6: Hardening

The runtime is now **measured, observable, recoverable, and audited** — with
**zero behavior change at defaults**. Every M6 subsystem is off, passive, or a
no-op unless explicitly enabled, so a default install is **byte-for-byte M5**
(invariant **I-23**, enforced by a parity test). No new agent capabilities were
added; this milestone hardens what M1–M5 shipped. Full design:
[RFC-0004](docs/rfc/0004-m6-hardening.md), [M6.md](docs/milestones/M6.md).

### Added
- **Benchmark suite** (`bench/`) — deterministic, `echo`/scripted-driven latency,
  memory (RSS), SQLite `EXPLAIN QUERY PLAN`, FTS, and event-stream measurements
  with committed baselines and a CI threshold guard (`make bench` / `make
  bench-ci`); environment captured in every report
  ([ADR-0020](docs/adr/0020-deterministic-benchmark-harness.md)).
- **Passive observability** (`atlas/obs/`) — a dependency-free in-process metrics
  registry (counters/gauges/histograms, Prometheus text exposition, fixed label
  cardinality), a `GET /metrics` endpoint (404 when disabled), a `GET /ready`
  dependency probe, and opt-in structured **JSON logging** with run/task
  correlation ids. All best-effort — recording can never raise into a run
  (**I-25**, [ADR-0022](docs/adr/0022-passive-dependency-free-observability.md)).
- **Crash recovery** (`atlas/recovery/`) — a startup **reconciler** brings runs
  left non-terminal by a crash to a consistent terminal state derived solely from
  the ledger, emitting an attributed recovery event; idempotent, on by default,
  and a no-op on a clean DB (**I-26**,
  [ADR-0021](docs/adr/0021-crash-recovery-reconcile-not-resume.md)).
- **Replay verification** — `fold_events`, the project's **canonical reducer**
  (ledger → run/answer/task state), shared by the reconciler and tests, with
  golden-ledger fixtures and a `sequence_is_intact` ordering check (**I-24**,
  [ADR-0023](docs/adr/0023-replay-verification-as-a-test.md)).
- **Integrity & determinism** — optional `PRAGMA quick_check` + FTS-drift check at
  startup (`ATLAS_DB_INTEGRITY_CHECK`), a `schema_meta` version row, and a
  repeated-run determinism test.
- **Evaluation harness** (`evals/`) — 8 goldens (single/multi-task, tool-use,
  retry, replan, partial, failure, memory-lift) driven through the real runtime on
  a scripted gateway; scores pass/fail + steps + tool calls into a committed
  `scorecard.md` + `results.json` (`make evals`). Lands the M5-deferred item.
- **Config** — `ATLAS_METRICS_ENABLED`, `ATLAS_LOG_FORMAT`,
  `ATLAS_RECOVERY_ENABLED`, `ATLAS_DB_INTEGRITY_CHECK` (all preserve M5 behavior at
  their defaults; recovery defaults on but only affects interrupted runs).
- **CI** — `.github/workflows/ci.yml`: Windows + Linux matrix, `pytest` +
  coverage floor, `ruff`, doc-lint (version/config parity as a test), `bench-ci`,
  `evals`, and frontend typecheck + build.
- **Audit reports** — `docs/{API,SECURITY,RELEASE,TECH_DEBT,DOC_AUDIT}.md`.
- **ADRs 0020–0024**; new invariants **I-23–I-28**.
- **Run history & replay page** — a `/history` list of past runs and a read-only,
  scrubber-style replay that re-folds a finished run's ledger through the same
  `runReducer` the live Run page uses, so live rendering and static replay share
  one code path ([ADR-0009](docs/adr/0009-event-sourced-plan-checklist.md)).
- **OpenAPI-generated frontend client** — `frontend/lib/generated/schema.ts` +
  `openapi-fetch`, generated from the backend's OpenAPI schema (`make gen-api`);
  `lib/types.ts` now aliases the generated schema, so a backend contract change
  surfaces as a frontend type error.
- **Frontend polish** — a navigation/accessibility pass (aria labels, focus
  handling, empty/loading states) and a finalized README/architecture doc set
  describing the current repository. Behavior-preserving; no runtime change.

### Changed
- Version → **0.6.0** (backend, frontend, `/health`).
- Storage: an `ix_runs_created` index (list pagination now uses an index scan,
  ~60 % faster p50/p95) and `PRAGMA temp_store=MEMORY`; both benchmark-justified
  with recorded before/after deltas, no externally observable behavior change.

### Migration
- Additive and idempotent (**I-27**): first start on a v0.5.0 database creates the
  `schema_meta` row and the `ix_runs_created` index — no manual step, no data loss,
  no existing table altered, downgrade-safe. Full Alembic remains deferred until
  Postgres.

## [0.5.0] — 2026-07-10 · Milestone M5: Episodic memory

Finished runs are now **distilled into lessons** and, on a related later goal,
**recalled at plan time** to improve planning — long-term memory the agent builds
from its own experience. Memory is **opt-in** (`ATLAS_MEMORY_ENABLED`, default
**off** → byte-for-byte M4) and **best-effort**: no recall/write/distillation
failure can ever affect a run. Full design:
[RFC-0003](docs/rfc/0003-m5-episodic-memory.md), [M5.md](docs/milestones/M5.md).

### Added
- **Episodic memory** (`atlas/memory/`) behind a `MemoryStore` seam
  (`EpisodicStore`, SQLite FTS5) so a future semantic/FAISS tier is a drop-in
  ([ADR-0004](docs/adr/0004-fts5-over-faiss.md),
  [ADR-0016](docs/adr/0016-episodic-memory-record.md)).
- **`memories` table + `memories_fts` FTS5 index** with sync triggers, created by
  an idempotent migration with a `LIKE` fallback when FTS5 is absent
  ([ADR-0017](docs/adr/0017-fts5-external-content-sync.md)); `run_id` is
  `ON DELETE SET NULL` so a lesson outlives its run.
- **Recall pipeline** — at plan time, ≤ `MEMORY_RECALL_K` relevant lessons are
  ranked (term-overlap relevance + salience/recency + outcome bias, min-score
  gate, char budget, redundancy filter) and injected into the planner as
  clearly-delimited untrusted hints; replans reuse them
  ([ADR-0018](docs/adr/0018-recall-at-plan-time.md)).
- **Write pipeline** — after a successful completion or a graceful partial, the
  run is distilled (LLM with a zero-LLM heuristic fallback) and upserted per run
  (idempotent), then the store is pruned; never on budget/cancel and never before
  the answer is delivered ([ADR-0019](docs/adr/0019-memory-best-effort-optional.md)).
- **Events** — `memory.recalled` (reserved in M4, now emitted, carrying the exact
  injected text for replay fidelity) and `memory.written`.
- **API** — `GET /memories` (list + `?q=` search + pagination), `GET
  /memories/{id}`, `DELETE /memories/{id}` (privacy purge), `POST
  /memories/{id}/pin` · `/unpin`. All additive; `MemoryView` is a new contract.
- **Frontend** — a **Memory** page (search, paginate, inspect, pin, forget); the
  Run page shows recalled lessons and a "saved to memory" note; the feed renders
  `memory.recalled`/`memory.written`.
- **Config** — nine `ATLAS_MEMORY_*` settings (all defaulted; memory off) and a
  `RunBudget.memory_calls` observability counter.

### Changed
- Version → **0.5.0** (backend, frontend, `/health`).

### Migration
- Additive and idempotent: first start on a v0.4.0 database creates `memories` +
  the FTS index/triggers — no manual step, no data loss, no existing table
  altered. Full Alembic remains deferred until Postgres.

## [0.4.0] — 2026-07-10 · Milestone M4: Self-correction agent

Each task's output is now **judged by a reflector** that decides `accept | retry |
replan | abort`. A poor attempt is retried with a critique (≤2/task); a wrong plan
is **replanned** from the current state (≤1/run, completed tasks immutable); an
unrecoverable state **gracefully aborts** with a partial answer. Per-run **budgets**
bound total model/tool calls. Reflection is opt-in
(`ATLAS_AGENT_ENABLE_REFLECTION`, default **off** → exact M3 behavior). Full design:
[RFC-0002](docs/rfc/0002-m4-self-correction.md), [M4.md](docs/milestones/M4.md).

### Added
- **Reflector** (`atlas/agent/reflector.py`) — a stateless verdict component: a
  deterministic pre-check (empty/`ERROR:` → retry, no LLM), then an LLM verdict
  tolerant-parsed with a bounded repair loop; unparseable/failed → `accept`
  (acceptance bias, [ADR-0011](docs/adr/0011-reflector-acceptance-bias.md)).
- **Retry loop** — a rejected attempt is re-run with a critique of the prior
  output folded into its context; every attempt is preserved.
- **Replanning** (`Planner.replan`) — a `replan` verdict (or retry-exhaustion)
  re-plans the *remaining* work into a new generation at monotonic indices;
  dropped tasks are `SKIPPED`, completed tasks immutable
  ([ADR-0013](docs/adr/0013-replan-from-current-state.md)).
- **Graceful abort** — an `abort`/exhaustion synthesizes a clearly-marked partial
  answer over completed outputs and finalizes `FAILED`-with-answer
  ([ADR-0014](docs/adr/0014-graceful-abort-partial-answer.md)).
- **Budgets** (`atlas/runtime/budget.py`) — a per-run `RunBudget` with hard caps
  on model/tool calls (plus attribution counters) enforced by thin
  `BudgetedGateway`/`BudgetedToolRegistry` wrappers; `BudgetExceeded` → `FAILED`,
  no partial ([ADR-0012](docs/adr/0012-central-budget-enforcement.md)).
- **`task_attempts` table** + `TaskAttemptRepository` — every attempt and its
  reflection persisted (`attempt_id` + `attempt_number`, `reflection_*`,
  `reflection_version`); `tasks` gains `attempt_count`, `replan_generation`,
  `parent_generation` via an idempotent light migration
  ([ADR-0015](docs/adr/0015-light-sqlite-migrations.md)).
- **Events** — `task.reflected`, `task.retrying`, `task.skipped`, `task.cancelled`,
  `plan.replanned`, `budget.exceeded`; executor `thought`/`tool.*` events now also
  carry `attempt` and `generation`. `TaskStatus` gains `RETRYING`, `CANCELLED`.
- **API** — `TaskView` exposes `attempt_count`/`replan_generation`/
  `parent_generation`; `RunView` exposes a derived `partial` flag. Both additive.
- **Frontend** — checklist renders retries/reflections/replans/skipped/cancelled;
  the feed renders the new events grouped by attempt; a partial-answer notice and a
  budget-exhausted banner.
- **Config** — `agent_enable_reflection` (off), `agent_max_retries` (2),
  `agent_max_replans` (1), `agent_max_model_calls` (60), `agent_max_tool_calls`
  (40), `agent_reflection_min_confidence` (0.0).

### Changed
- With reflection **enabled**, a task no longer fails the whole run on the first
  bad attempt — it is retried/replanned/aborted per the ladder. With reflection
  **disabled** (default) behavior is byte-for-byte M3 (invariant I-13).

### Migration
- On first start against a v0.3.0 database, `Database.create_all` adds the new
  `tasks` columns via a guarded `ALTER TABLE … ADD COLUMN` and creates
  `task_attempts` — idempotent, no data loss, no manual step
  ([ADR-0015](docs/adr/0015-light-sqlite-migrations.md)).

## [0.3.0] — 2026-07-09 · Milestone M3: Planning agent (task list)

A goal is now **planned** into an ordered list of ≤5 tasks, each executed in turn
by the M2 ReAct loop, then **synthesized** into a final answer — the plan ticks
live on a checklist and the whole run stays persisted and replayable.

### Added
- **Planner** (`atlas/agent/planner.py`) — one LLM call → `{"tasks":[…]}` (≤5),
  tolerant-parsed with a bounded repair loop, then defensively normalized (drop
  empty tasks, null unknown `suggested_tool` hints, hard-cap, single-task
  fallback). Invalid after repair → `PlannerError` → run `FAILED` during PLANNING.
- **Context Builder** (`atlas/agent/context.py`) — assembles each task's executor
  prompt from the goal, task, and truncated prior-task outputs (summarize-at-
  source, design §1.11) via the typed `TaskContext` seam.
- **Synthesizer** (`atlas/agent/synthesizer.py`) — a distinct LLM call composes
  the final answer from task outputs; single-task plans short-circuit
  ([ADR-0010](docs/adr/0010-synthesis-as-distinct-call.md)).
- **`jsonio`** (`atlas/agent/jsonio.py`) — tolerant JSON extraction factored out
  of `envelope.py`, shared by the planner and the action envelope.
- **`tasks` table** (`persistence/models.py`) + `TaskRepository` — ordered per-run
  task rows with an FSM (`PENDING → RUNNING → DONE|FAILED`), cascade-deleted with
  the run.
- **Contracts** (`agent/schemas.py`) — `PlannedTask`, `Plan`, `TaskView`,
  `PriorTaskOutput`, `TaskContext` (all additive to the frozen file).
- **Events** — begins emitting `plan.created` / `task.started` / `task.completed`
  and adds `task.failed`; executor `thought`/`tool.*` events now carry `task_id`.
- **API** — `GET /runs/{id}/tasks` (event-sourced projection,
  [ADR-0009](docs/adr/0009-event-sourced-plan-checklist.md)).
- **Config** — `agent_max_tasks` (5), `context_prior_output_chars` (600),
  `agent_force_synthesis` (off).
- **Frontend** — `PlanChecklist` component, task-state tracking in
  `useRunStream`, plan section on the Run page, plan/task rows in the event feed.
- **Tests** — 23 new (planner, context, synthesizer, runtime-m3, persistence,
  API); **86 total**.

### Changed
- **`RunManager`** now orchestrates PLANNING → per-task RUNNING → SYNTHESIS,
  persisting task status and finalizing the run. A task failure fails the whole
  run in M3 (partial plan + outputs preserved); recovery is M4.
- **`Executor` is execution-only** (RFC-0001 decision 4): `run()` takes prebuilt
  messages from the Context Builder and a `task_id`, instead of a goal string.
- **`echo` provider is planner-aware** — it returns a valid single-task plan for a
  planner prompt so the model-free dev/CI path completes a full plan → execute →
  answer cycle. The strict planner contract is covered by the FakeLLM tests.
- **PLANNING** run state activated in the runtime and surfaced in the UI.

### Design
- [RFC-0001](docs/rfc/0001-m3-planning-agent.md) (approved),
  [ADR-0009](docs/adr/0009-event-sourced-plan-checklist.md),
  [ADR-0010](docs/adr/0010-synthesis-as-distinct-call.md).

## [0.2.0] — 2026-07-09 · Milestone M2: Tool-using agent (single task)

The run is now driven by a hand-built **ReAct loop**: the agent reasons, calls
tools, observes results, and finishes — streamed as live, persisted events.

### Added
- **Tools** (`atlas/tools/`)
  - `Tool` ABC + `ToolResult` + `ToolSpec`; a `ToolRegistry` that validates
    arguments, enforces a timeout, and traps **every** failure into an
    observation (no exception crosses the executor loop — design §1.2c).
  - Four P0 tools (design §5): `calculator` (safe AST eval, no `eval`),
    `file_read`/`file_write` (path-jailed workspace), `web_search` (ddgs),
    `web_fetch` (HTML→text, truncated at source).
  - `build_registry(settings)` and `GET /tools`.
- **Agent** (`atlas/agent/`)
  - Flat JSON action envelope (`tool_call | finish`) with tolerant parsing and a
    bounded repair loop (`envelope.py`), prompt templates (`prompts.py`).
  - `Executor` — the ReAct loop (≤ `agent_max_iterations`), emitting
    `thought` / `tool.call` / `tool.result` events; graceful degradation on
    unparseable turns (see [ADR-0008](docs/adr/0008-envelope-and-graceful-degradation.md)).
- **FakeLLM** `ScriptedGateway` (`llm/providers/scripted.py`) — deterministic
  agent tests with no model or network (design §10.5).
- **Config** — `agent_max_iterations`, `agent_repair_attempts`, `workspace_dir`,
  `tool_timeout_seconds`, `web_search_max_results`, `web_fetch_max_chars`.
- **Frontend** — event feed renders thoughts, tool calls, and tool results.
- **Tests** — 36 new (tools, envelope, executor, end-to-end runtime); **60 total**.

### Changed
- `RunManager` now drives the `Executor` instead of the M1 thin chat; it still
  streams the final answer as `answer.token` events and owns run lifecycle.
- **M1 audit:** removed dead code — four unused dependency accessors
  (`get_db`, `get_hub`, `get_run_manager`, `get_settings`) from `api/deps.py`;
  routes use the single `get_context` accessor. Verified M1 matches the design doc.

## [0.1.0] — 2026-07-09 · Milestone M1: Walking skeleton

The full stack runs end-to-end: a goal becomes a run whose answer streams to the
browser as persisted, replayable events.

### Added
- **Backend (FastAPI)**
  - Environment-driven config (`ATLAS_`-prefixed `pydantic-settings`) and logging.
  - Frozen contracts: `agent/schemas.py` (run/task FSMs, API models) and
    `events/types.py` (event envelope with monotonic `seq`).
  - Persistence: async SQLAlchemy engine with SQLite **WAL**; `runs` and `events`
    tables; repository pattern (single-writer discipline).
  - `LLMGateway` seam with two providers: `ollama` (streaming `/api/chat`) and
    `echo` (deterministic, dependency-free — dev/CI without a model).
  - `emit(event)` → events table (`seq`) + in-process `EventHub` for WebSocket
    fan-out.
  - `RunManager`: thin goal→streamed-answer flow, cooperative cancellation,
    failure containment.
  - REST: `GET /health`, `POST /runs`, `GET /runs`, `GET /runs/{id}`,
    `GET /runs/{id}/events?after=N`, `POST /runs/{id}/cancel`.
  - WebSocket `GET /runs/{id}/stream?after=N` with subscribe-then-backfill
    ordering (replay for free).
  - 24 tests (pytest, `echo` provider, temp DB) — all passing.
- **Frontend (Next.js 14 · TS · Tailwind)**
  - Run page v0: goal input, live status badge, streaming answer, event feed.
  - `useRunStream` hook with WebSocket streaming and `after`-cursor reconnection.
  - Typed backend client mirroring the frozen contracts.
- **Infrastructure & docs**
  - `docker-compose.yml` (Ollama + backend + frontend) and per-service
    Dockerfiles; `Makefile` with milestone targets (`test-m1`, `demo-m1`, …).
  - README, backend/frontend READMEs, `docs/ARCHITECTURE.md`, `docs/TODO.md`,
    milestone spec `docs/milestones/M1.md`, and ADRs 0001–0007.

### Notes / assumptions
- Local Python is 3.14 via `py`; dependency floors were chosen so prebuilt wheels
  exist (no source builds). See [ADR-0006](docs/adr/0006-python314-wheels.md).
- Ollama is not installed in the dev environment, so M1 is demonstrated with the
  `echo` provider; the Ollama provider is implemented and used by default.
