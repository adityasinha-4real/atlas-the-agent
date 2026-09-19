# ATLAS Backend

FastAPI service implementing the ATLAS agentic runtime: a goal is planned into
an ordered task list, each task executed by a tool-using ReAct loop, graded by a
reflector that can retry/replan/abort, and the outputs synthesized into a final
answer — bounded by per-run budgets, with optional episodic memory recalled at
plan time and written on finish. Python 3.11+ (developed on 3.14; Docker image
uses 3.12).

## Setup

```bash
py -m venv .venv
.venv/Scripts/python.exe -m pip install --upgrade pip
.venv/Scripts/python.exe -m pip install --only-binary=:all: -r requirements-dev.txt
```

> `--only-binary=:all:` forces prebuilt wheels — important on Python 3.14 where
> some packages have no source-build toolchain on Windows.

## Run

```bash
# Deterministic dev mode (no Ollama):
ATLAS_LLM_PROVIDER=echo .venv/Scripts/python.exe -m uvicorn atlas.main:app --reload
# With a real local model (Ollama must be running):
.venv/Scripts/python.exe -m uvicorn atlas.main:app --reload
```

Interactive API docs: http://localhost:8000/docs

## Test

```bash
.venv/Scripts/python.exe -m pytest
```

Tests use the `echo` provider and a temporary SQLite database — no Ollama, fully
deterministic, safe for CI.

## Layout

| Package | Responsibility |
|---|---|
| `atlas/core` | Config (`ATLAS_`-prefixed settings) and logging |
| `atlas/agent` | **Frozen** contracts (`schemas.py`), `jsonio`, action envelope, `Planner`, `ContextBuilder`, `Executor`, `Reflector`, `Synthesizer`, prompts |
| `atlas/tools` | `Tool` ABC + `ToolResult`, registry, and the 4 P0 tools |
| `atlas/events` | Event types, in-process hub, `emit()` seam |
| `atlas/llm` | `LLMGateway` ABC + `ollama` / `echo` / `scripted` providers |
| `atlas/memory` | `MemoryStore` seam over SQLite FTS5 — recall at plan time, distill-and-write on finish |
| `atlas/persistence` | Async engine (WAL), ORM models (`runs`/`events`/`tasks`/`memories`), repositories |
| `atlas/runtime` | `RunManager` (plan → per-task execute → reflect → synthesize) and per-run budgets |
| `atlas/obs` | Dependency-free metrics registry (Prometheus text), opt-in JSON logging |
| `atlas/recovery` | Startup crash reconciler + canonical replay reducer, shared with replay verification |
| `atlas/api` | App factory, REST routes, WebSocket stream |

## API surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness + provider info |
| `GET` | `/ready` | Readiness probe (DB reachable, FTS5 present, provider configured) |
| `GET` | `/metrics` | Prometheus text metrics (404 when `ATLAS_METRICS_ENABLED=false`) |
| `GET` | `/tools` | Registered tool specs |
| `POST` | `/runs` | Create and start a run (`{goal}`) |
| `GET` | `/runs` | List recent runs |
| `GET` | `/runs/{id}` | Run detail (status, answer) |
| `GET` | `/runs/{id}/tasks` | Task-list projection (checklist/replay) |
| `GET` | `/runs/{id}/events?after=N` | Event ledger (backfill/replay) |
| `POST` | `/runs/{id}/cancel` | Request cooperative cancellation |
| `WS` | `/runs/{id}/stream?after=N` | Live event stream (resumable) |
| `GET` | `/memories?q=&limit=&offset=` | List memories, or full-text search when `q` is given |
| `GET` | `/memories/{id}` | Memory detail |
| `DELETE` | `/memories/{id}` | Forget (privacy purge) |
| `POST` | `/memories/{id}/pin` / `/unpin` | Pin so a memory is never pruned and is recall-preferred |

The OpenAPI schema for this surface is dumped to `frontend/openapi.json`
(`make gen-api`) and drives the frontend's generated TypeScript client — see
[`frontend/README.md`](../frontend/README.md#api-client-generated).

## Run flow

`RunManager` drives **PLANNING → per-task RUNNING → SYNTHESIS**: the `Planner`
turns the goal into an ordered ≤5-task list (`tasks` table), optionally seeded
with lessons recalled from episodic memory; the `ContextBuilder` assembles each
task's prompt; the reused `Executor` runs the ReAct loop per task; and the
`Synthesizer` composes the final answer (single-task plans short-circuit). When
`ATLAS_AGENT_ENABLE_REFLECTION=true`, the `Reflector` grades each task's output
and can `retry` it, `replan` the remaining tasks, or gracefully `abort` with a
partial answer — all bounded by per-run model/tool call budgets. When
`ATLAS_MEMORY_ENABLED=true`, finished runs are distilled into a memory and
written on completion. The plan checklist is event-sourced (`plan.created` +
`task.*`), and the full event ledger drives live streaming, crash recovery, and
the frontend's History/Replay page identically (ADR-0009).

## Tools

`calculator` (safe AST eval), `file_read` / `file_write` (path-jailed workspace),
`web_search` (ddgs), `web_fetch` (HTML→text). The **registry** is the safety
boundary: it validates arguments, enforces a timeout, and turns every failure
into a `ToolResult` observation — no tool exception crosses the executor loop.

## Design invariants

- **Single-writer discipline:** all DB writes go through repositories; `emit()`
  serializes sequence allocation so per-run `seq` is strictly monotonic.
- **No exceptions cross the run boundary:** the runtime converts every failure
  (planner, executor, synthesis, unexpected) into a `run.failed` event; the tool
  registry converts every tool failure into an observation.
- **The event log is the source of truth:** run rows are a projection; the
  ledger drives trace, replay, and WebSocket backfill.
