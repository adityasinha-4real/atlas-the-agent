# ATLAS

**A hand-built agentic AI runtime — plan, act, reflect, recover — over a local LLM.**

ATLAS turns a natural-language *goal* into an autonomous run: it **plans** an
ordered task list, executes each task with a hand-built **ReAct loop** (tools:
`calculator`, `web_search`, `web_fetch`, `file_read`/`file_write`), and
**judges its own output with a reflector** that can `retry` a poor attempt,
`replan` a wrong approach, or gracefully `abort` with a partial answer — all
bounded by per-run **budgets**. It runs on a local model (Ollama
`qwen2.5:7b-instruct`) with no cloud dependency.

Opt-in **episodic memory** distills finished runs into lessons (SQLite FTS5)
and **recalls them at plan time**, browsable on a **Memory** page. Every run
streams live over WebSocket and is fully **replayable** from its event ledger —
past runs are listed and replayed scrubber-style on a **History** page, reusing
the exact same reducer that drove the live view. The frontend's REST client is
**generated from the backend's OpenAPI schema**, so a contract change surfaces
as a type error, not silent drift. The runtime is also **measured, observable,
and recoverable**: a deterministic benchmark suite, passive observability
(`/metrics`, `/ready`, JSON logs), startup crash recovery, and an offline eval
harness. Self-correction (`ATLAS_AGENT_ENABLE_REFLECTION`) and memory
(`ATLAS_MEMORY_ENABLED`) are **opt-in, default off**. See
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) and the
[development history](docs/TODO.md) for how this came together.

---

## Why this project

The design is deliberately *lean*: a sequential task list (not a DAG), planner
re-invocation (not a replanner module), SQLite FTS5 episodic memory (not a vector
DB in v1), and a single `emit()` event log that doubles as trace, replay source,
and WebSocket backfill. The reasoning behind each cut is captured as an
[Architecture Decision Record](docs/adr/). The guiding constraint throughout:
*small local models are erratic*, so every layer is built to detect and recover
from that.

Full specification: [`ATLAS-Design-Review-V2.md`](ATLAS-Design-Review-V2.md).

## Architecture at a glance

```
┌─────────── FRONTEND (Next.js · TS · Tailwind) ────────────┐
│  Run · History/Replay · Memory pages                       │
│  Typed REST client generated from the OpenAPI schema        │
└───────────────────────┬───────────────────────────────────┘
              REST + WebSocket (seq-numbered, replayable)
┌───────────────────────▼───────────────────────────────────┐
│                     FASTAPI BACKEND                        │
│  Run FSM: CREATED → PLANNING → RUNNING → DONE              │
│           (→ FAILED, → CANCELLED, RUNNING ⇄ PAUSED)        │
│  RunManager: Planner → per-task Executor (ReAct ≤5)       │
│              → Synthesizer  ·  Context Builder  ·  Reflector│
│  MemoryStore (FTS5): recall at plan time · write on finish │
│  Tool Registry: calculator · web_search · web_fetch · files│
│  LLMGateway (Ollama | echo)  ·  emit() → events table + WS │
│  Repositories over SQLite (WAL): runs · events · tasks · memories│
└────────────────────────────────────────────────────────────┘
                    Ollama · qwen2.5:7b-instruct
```

| Component | Implementation |
|---|---|
| Backend | FastAPI service (`backend/atlas/`) — see [`backend/README.md`](backend/README.md) |
| Frontend | Next.js 14 app (`frontend/`) — see [`frontend/README.md`](frontend/README.md) |
| Planner | Turns a goal into an ordered ≤5-task list (`atlas/agent/planner.py`) |
| ReAct executor | Think → act → observe loop per task, ≤5 iterations (`atlas/agent/executor.py`) |
| Reflector | Grades a task's output; `retry` / `replan` / `abort` verdicts, opt-in (`atlas/agent/reflector.py`) |
| Episodic memory | SQLite FTS5 store; recalls lessons at plan time, writes on finish, opt-in (`atlas/memory/`) |
| Persistence | Async SQLAlchemy over SQLite (WAL): runs, tasks, events, memories (`atlas/persistence/`) |
| Event streaming | Every state change is an ordered, persisted event; WebSocket subscribe + backfill (`atlas/events/`, `atlas/api/ws.py`) |
| Run history & replay | Past-run list and a read-only scrubber that re-folds a finished run's ledger through the same reducer the live page uses (`frontend/app/history/`, [ADR-0009](docs/adr/)) |
| OpenAPI-generated frontend client | `frontend/lib/generated/schema.ts` + `openapi-fetch`, generated from the backend's OpenAPI schema (`make gen-api`) |

More detail: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

## Quick start

### Option A — local dev, no model required (echo provider)

```bash
# Backend (terminal 1)
cd backend
py -m venv .venv
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements-dev.txt
ATLAS_LLM_PROVIDER=echo .venv/Scripts/python.exe -m uvicorn atlas.main:app --reload
# → http://localhost:8000  (docs at /docs)

# Frontend (terminal 2)
cd frontend
npm install
npm run dev
# → http://localhost:3000
```

Open http://localhost:3000, type a goal, and watch the plan checklist and live
event feed. Past runs are listed on **History** (`/history`) and replayable from
their event ledger; recalled/saved lessons are browsable on **Memory**
(`/memory`). The `echo` provider plans a single task equal to the goal and
answers it directly, so the full multi-task plan → execute → synthesize flow
needs Ollama — but it is covered deterministically by the FakeLLM tests
(`make test`).

### Option B — full stack with a real model (Docker)

```bash
docker compose up -d ollama
docker compose exec ollama ollama pull qwen2.5:7b-instruct
docker compose up --build
# Frontend → http://localhost:3000, Backend → http://localhost:8000
```

> On Windows the venv interpreter is `.venv/Scripts/python.exe`; on macOS/Linux
> it is `.venv/bin/python`. The [`Makefile`](Makefile) wraps these commands
> (`make setup`, `make test`, `make run-backend`, `make up`).

## Testing

```bash
cd backend && .venv/Scripts/python.exe -m pytest              # 307 tests, no Ollama needed
cd backend && .venv/Scripts/python.exe -m ruff check atlas tests  # lint
cd frontend && npm run typecheck && npm run build             # frontend type-safety + build
```

All agent-logic tests run against the deterministic `echo` provider and a
scripted **FakeLLM**, so CI never needs a model. The [`Makefile`](Makefile)
wraps the common combinations: `make test`, `make lint`, `make build-frontend`.
See [`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md).

### Benchmarks & evals

```bash
backend/.venv/Scripts/python.exe -m bench --ci   # short benchmark subset + threshold guard
backend/.venv/Scripts/python.exe -m evals        # golden evals -> evals/scorecard.md
```

Both run from the repo root against the deterministic `echo`/scripted providers
— no Ollama, no network. `make bench` runs the full benchmark suite. See
[`bench/README.md`](bench/README.md) and [`evals/README.md`](evals/README.md).

## Configuration

Everything is environment-driven with the `ATLAS_` prefix; nothing is hardcoded.
Copy [`backend/.env.example`](backend/.env.example) → `.env` and
[`frontend/.env.local.example`](frontend/.env.local.example) → `.env.local`.

| Variable | Default | Purpose |
|---|---|---|
| `ATLAS_LLM_PROVIDER` | `ollama` | `ollama` or `echo` (dev/CI) |
| `ATLAS_LLM_MODEL` | `qwen2.5:7b-instruct` | Ollama model tag |
| `ATLAS_LLM_BASE_URL` | `http://localhost:11434` | Ollama endpoint |
| `ATLAS_DATABASE_URL` | `sqlite+aiosqlite:///./data/atlas.db` | Async SQLite URL |
| `ATLAS_CORS_ORIGINS` | `http://localhost:3000` | Allowed frontend origins |
| `ATLAS_AGENT_MAX_ITERATIONS` | `5` | Executor ReAct loop cap |
| `ATLAS_AGENT_MAX_TASKS` | `5` | Planner task-list cap |
| `ATLAS_AGENT_ENABLE_REFLECTION` | `false` | Self-correction (retry/replan/abort); off = single-pass execution |
| `ATLAS_MEMORY_ENABLED` | `false` | Episodic memory (recall + write); off = memory store unused |
| `ATLAS_WORKSPACE_DIR` | `./data/workspace` | Path jail for file tools |
| `ATLAS_METRICS_ENABLED` | `false` | Metrics registry + `/metrics` (off → endpoint returns 404) |
| `ATLAS_LOG_FORMAT` | `text` | Log format: `text` or `json` |
| `ATLAS_RECOVERY_ENABLED` | `true` | Crash reconcile at startup (no-op on a clean DB) |
| `ATLAS_DB_INTEGRITY_CHECK` | `false` | `PRAGMA quick_check` at startup (fast start off) |

The full set (including the observability/recovery knobs) is in
[`backend/.env.example`](backend/.env.example); a test asserts every setting is
documented there.

## Repository layout

```
atlas/
├── backend/            FastAPI service (Python)
│   └── atlas/
│       ├── api/        app factory, REST routes, WebSocket stream
│       ├── agent/      frozen schemas, action envelope, executor, prompts
│       ├── tools/      Tool ABC, registry, 4 P0 tools
│       ├── events/     event types, hub, emit()
│       ├── llm/        LLMGateway + providers (ollama, echo, scripted)
│       ├── memory/     MemoryStore seam, EpisodicStore (FTS5), recall/write
│       ├── obs/        passive metrics registry (Prometheus text)
│       ├── recovery/   crash reconciler + canonical replay reducer
│       ├── persistence/ SQLAlchemy models, repositories, engine
│       └── runtime/    RunManager (orchestration) + per-run budgets
├── frontend/           Next.js app (Run · History/Replay · Memory pages)
├── bench/              deterministic benchmark suite + baselines
├── evals/              golden eval harness → scorecard.md
├── docs/
│   ├── ARCHITECTURE.md · API.md · SECURITY.md · RELEASE.md
│   ├── TECH_DEBT.md · DOC_AUDIT.md   (audit reports)
│   ├── TODO.md         roadmap / progress
│   ├── milestones/     per-milestone specs (M1.md …)
│   ├── rfc/            design RFCs (RFC-0001 …)
│   └── adr/            architecture decision records
├── .github/workflows/  CI (matrix, coverage, bench-ci, evals)
├── docker-compose.yml
└── CHANGELOG.md
```

## Development history

The runtime described above was built incrementally, each stage adding one
capability without changing what already worked: a walking skeleton, a
tool-using agent, planning, self-correction, episodic memory, hardening
(benchmarks/observability/recovery/evals), and finally packaging/UX polish for
release. Full progress log: [`docs/TODO.md`](docs/TODO.md).

## License

MIT (see `LICENSE`).
