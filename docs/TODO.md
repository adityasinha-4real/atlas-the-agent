# ATLAS Roadmap & Progress

Vertical milestones (design doc §9). Every milestone ends with a runnable,
demo-ready app. This file is the single source of truth for progress.

## Legend
✅ done · 🟡 in progress · ⬜ not started

---

## ✅ M1 — Walking skeleton
**Demo:** type a goal in the web UI → backend streams the model's answer as live,
persisted events.

- [x] FastAPI app + env config + logging
- [x] Frozen contracts (`schemas.py`, `events/types.py`)
- [x] SQLite (WAL) + `runs`/`events` tables + repositories
- [x] `LLMGateway` seam + `ollama` + `echo` providers
- [x] `emit()` → events table (`seq`) + WebSocket hub
- [x] `RunManager` thin flow + cooperative cancel + failure containment
- [x] REST routes + WebSocket stream (subscribe-then-backfill)
- [x] Next.js Run page v0 (goal, status, streaming answer, event feed)
- [x] Docker compose + Dockerfiles + Makefile
- [x] Tests (24, green) + docs + ADRs

## ✅ M2 — Tool-using agent (single task)
**Demo:** "What is 15% of France's population?" → UI shows think→search→calculate→answer.
- [x] `Tool` ABC + `ToolResult` + registry (path-jail; timeout + error trapping)
- [x] 4 P0 tools: `web_search` (ddgs), `web_fetch`, `file_read`/`file_write`, `calculator`
- [x] JSON-mode envelope + repair loop (≤2) + graceful degradation (ADR-0008)
- [x] Executor ReAct loop (≤5 iters), observations-not-exceptions
- [x] `FakeLLM` (`ScriptedGateway`) for deterministic agent tests
- [x] Event-feed UI for thoughts/tool calls/results
- [x] `GET /tools` route; 60 tests green, ruff clean

## ✅ M3 — Planning agent
**Demo:** multi-step goal → plan checklist renders → tasks tick live → synthesized answer.
- [x] Planner (ordered list, ≤5 tasks, few-shot tight plans, repair + normalize)
- [x] `tasks` table + task FSM (PENDING→RUNNING→DONE/FAILED; SKIPPED reserved for M4)
- [x] Context builder v1 (typed `TaskContext`, summarize-at-source, token budget)
- [x] Synthesizer (distinct call, single-task short-circuit — ADR-0010)
- [x] PLANNING state; `plan.created`/`task.*` events; `task_id`-tagged executor events
- [x] Checklist UI (event-sourced — ADR-0009) + `GET /runs/{id}/tasks`; cancel surfaced
- [x] 86 tests green, ruff clean, frontend typecheck + build green

## ✅ M4 — Self-correcting agent (v0.4.0)
**Demo:** a task fails → UI shows critique → retry succeeds; plus one replan and a
graceful partial abort. Reflection opt-in; off = exact M3.
- [x] Reflector: deterministic pre-check + 4 verdicts (accept/retry/replan/abort), acceptance bias
- [x] Retry policy (≤2), replan-via-planner (≤1/run) with generations, graceful abort + partial
- [x] Per-run budgets (model/tool hard caps) via gateway/registry wrappers
- [x] `task_attempts` table + light migration; attempt/generation on events & views
- [x] Retry/reflection/replan/skip/cancel badges + partial/budget banners in UI
- See [M4.md](milestones/M4.md), [RFC-0002](rfc/0002-m4-self-correction.md), ADR-0011..0015

## ✅ M5 — Episodic memory (v0.5.0)
**Demo:** run goal A, then related goal B → "recalled lesson from run #A".
- [x] Episodic memory (SQLite FTS5) behind `MemoryStore`; recall at plan time
- [x] `memories` table + `memories_fts` index (light migration, LIKE fallback)
- [x] Write pipeline (distill on finish, best-effort, idempotent, prune)
- [x] `memory.recalled`/`memory.written` events; `/memories` API (list/search/get/delete/pin)
- [x] Memory page (search, inspect, pin, forget) + recall/write rendering on the Run page
- [x] Reflection/memory both opt-in, default off; disabled = exact M4 (I-15)
- See [M5.md](milestones/M5.md), [RFC-0003](rfc/0003-m5-episodic-memory.md), ADR-0016..0019
- [x] Eval harness + 8 goldens → `scorecard.md` *(landed in M6 Phase 4)*

## ✅ M6 — Hardening (v0.6.0)
**Scope shift from the original sketch:** M6 is **hardening-only** — robustness,
not new agent capabilities. `code_sandbox`/`PAUSED`/crash-**resume** were dropped
in favor of reconcile-to-terminal recovery (ADR-0021); the History/replay page
(planned for M7) shipped early alongside M6 in v0.6.0. Full design: [RFC-0004](rfc/0004-m6-hardening.md), [M6.md](milestones/M6.md).
- [x] Benchmark suite (`bench/`) — deterministic latency/RSS/query-plan baselines + CI guard (ADR-0020)
- [x] Passive observability (`atlas/obs/`) — metrics registry, `/metrics`, `/ready`, JSON logs (ADR-0022)
- [x] Crash recovery (`atlas/recovery/`) — startup reconciler, ledger-derived, idempotent (ADR-0021)
- [x] Replay verification — `fold_events` canonical reducer + golden ledgers (ADR-0023)
- [x] Integrity (`PRAGMA quick_check`) + `schema_meta` row + determinism test
- [x] Eval harness (`evals/`) — 8 goldens + memory-lift → `scorecard.md`
- [x] Storage optimization — `ix_runs_created` index + `temp_store=MEMORY` (benchmark-justified)
- [x] CI matrix (Windows + Linux), coverage floor, bench-ci, evals, doc-lint
- [x] Audits — API, security, config, tech-debt, docs (`docs/{API,SECURITY,RELEASE,TECH_DEBT,DOC_AUDIT}.md`)
- [x] New invariants I-23..I-28; ADR-0020..0024; version → 0.6.0

## 🟡 M7 — Ship (v0.7.0)
**Demo:** clean-machine `docker compose up` → demo in <10 min.
- [x] History page + run replay (`/history`, `/history/[runId]`; re-folds `GET /runs/{id}/events`
      through the live `runReducer`) — shipped early in v0.6.0
- [x] OpenAPI-generated frontend client (`lib/generated/schema.ts` + `openapi-fetch`) — shipped in v0.6.0
- [x] Config fix: comma-separated list settings (`ATLAS_CORS_ORIGINS`, `ATLAS_MEMORY_WRITE_OUTCOMES`)
      from env/`.env` no longer crash startup; regression tests
- [x] Packaging polish: `frontend/.env.local.example` committed; `.env` kept out of the backend
      build context; model-free `docker-compose.echo.yml`; Docker build + echo-mode smoke test run locally
- [x] CI: invalid workflow YAML fixed; OpenAPI + generated-client drift gates
- [x] CI badge in README (goes green once the code is pushed to `adityasinha-4real/atlas-the-agent`)
- [x] Eval scorecard summarized and linked in the README
- [x] Version → 0.7.0
- [ ] **Manual:** README demo GIF (goal → plan → visible retry → answer); needs Ollama
- [ ] **Manual:** clean-machine `docker compose up --build` smoke test
- [ ] **Manual:** push the release to GitHub and confirm the first CI run is green
- See [M7.md](milestones/M7.md) (includes the demo-recording script)

---

## Cross-cutting backlog
- [x] Generate the frontend TS client from backend OpenAPI (replace hand-kept `lib/types.ts`) — v0.6.0
- [x] OpenAPI schema-drift gate in CI — v0.7.0
- [x] CI workflow (backend pytest + ruff + coverage, frontend typecheck + build, bench-ci, evals) — M6
- [x] Structured JSON logging option for production (`ATLAS_LOG_FORMAT=json`) — M6

## Deferred (post-M7)
- Memory recall at execution/reflection time (currently plan time only; ADR-0018)
- Semantic/vector memory tier behind the `MemoryStore` seam (ADR-0004)
- Postgres + Alembic migrations (ADR-0015); a real event bus for multi-process scale
- `code_sandbox` tool, `PAUSED` state, crash *resume* (dropped in M6, ADR-0021)
- Frontend lint (ESLint) and unit tests for `runReducer`; `next@16` upgrade to clear two advisories
- Auth / multi-user (ATLAS is a local single-user tool)

## Known limitations (M1–M5)
- Episodic memory is **opt-in** (`ATLAS_MEMORY_ENABLED`, default off) and
  plan-time only; recall depends on the cross-run store snapshot, so the
  `memory.recalled` event records the exact injected text to keep per-run replay
  ledger-derived. Semantic/vector recall is deferred behind the `MemoryStore` seam.
- Cancellation is cooperative (checked during planning, between tasks, between
  executor iterations, before/after reflection and replan, and between streamed
  chunks); a single long, token-less model call still delays observation — the
  per-run model-call **budget** (M4) is the hard backstop.
- With reflection **enabled**, retry/replan/graceful-abort recover a failing task;
  with it **off** (default) a task failure still fails the whole run (exact M3).
- In-memory `EventHub` is single-process; horizontal scale needs a real bus (§7).
- ~~`lib/types.ts` is hand-maintained until the OpenAPI generator lands.~~ Resolved in
  v0.6.0: `lib/types.ts` aliases the generated schema.
- No full migration tool yet: `create_all` adds new tables, and M4's idempotent
  `ALTER TABLE … ADD COLUMN` guard handles additive columns (ADR-0015); Alembic is
  deferred until Postgres is on the horizon (§7).
- `web_search`/`web_fetch` need network, so they are verified by unit tests for
  arg-validation and graceful failure, not live calls in CI.
- With the `echo` provider the planner returns a single task equal to the goal and
  the executor answers it directly (no tool loop); the full multi-task plan →
  execute → synthesize flow needs Ollama or the FakeLLM (deterministic tests cover it).
