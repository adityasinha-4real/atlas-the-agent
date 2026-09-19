# ATLAS API reference & stability policy

**Audit: API consistency (RFC-0004 §17).** This is the frozen REST/WebSocket
surface as of **v0.6.0**. Every route has an explicit Pydantic response model (no
bare dicts on typed routes), consistent error shapes, and is documented in
`/openapi.json`. A path-set snapshot test (`test_openapi_documents_memory_routes`)
guards accidental additions/removals; version parity is asserted by
`test_docs_parity.py`.

## Base

- Base URL: `http://localhost:8000`. Interactive docs at `/docs`, schema at
  `/openapi.json`.
- All bodies are JSON. Times are ISO-8601 UTC. Ids are opaque strings.
- CORS is restricted to `ATLAS_CORS_ORIGINS` (default `http://localhost:3000`);
  no wildcard ships.

## Endpoints

### System

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/health` | `HealthView` | Liveness + `version` (== package == frontend). Always 200. |
| GET | `/ready` | `{ready, version, checks}` | Dependency probe (DB, FTS5, provider). **200** ready / **503** not. Read-only. |
| GET | `/metrics` | Prometheus text | **404** when `ATLAS_METRICS_ENABLED=false` (default); else `text/plain` exposition. |
| GET | `/tools` | `list[ToolSpec]` | Registered tool specs. |

### Runs

| Method | Path | Response | Notes |
|---|---|---|---|
| POST | `/runs` | `RunView` (201) | Body `{goal}`. Starts a run. |
| GET | `/runs` | `list[RunSummary]` | `?limit=&offset=` (newest first). |
| GET | `/runs/{id}` | `RunView` | **404** if unknown. |
| GET | `/runs/{id}/events` | `list[Event]` | `?after=N` returns events with `seq > N` (replay/backfill source). |
| GET | `/runs/{id}/tasks` | `list[TaskView]` | Projection over the `tasks` table. |
| POST | `/runs/{id}/cancel` | `{run_id, cancel_requested}` (202) | Cooperative cancel; idempotent on terminal runs. **404** if unknown. |
| WS | `/runs/{id}/stream` | `Event` frames | `?after=N`: subscribe → backfill `seq > N` → live. Close `4404` if unknown. |

### Memory (M5)

| Method | Path | Response | Notes |
|---|---|---|---|
| GET | `/memories` | `list[MemoryView]` | `?q=` FTS search + `?limit=&offset=`. Empty when memory disabled. |
| GET | `/memories/{id}` | `MemoryView` | **404** if unknown. |
| DELETE | `/memories/{id}` | 204 | Privacy purge (hard delete). |
| POST | `/memories/{id}/pin` · `/unpin` | `MemoryView` | Pin exempts from prune. |

## Conventions

- **Errors:** `404` unknown id, `422` request validation (FastAPI default shape),
  `500` never expected (probes and best-effort paths swallow their own faults).
  `/ready` uses `503` for "up but not serving"; `/metrics` returns `404` when
  disabled. Cancel is idempotent (no `409`): a cancel on a terminal run returns
  `202` with `cancel_requested=false` rather than an error.
- **Pagination:** `limit`/`offset` mean the same on every list route — `offset`
  skips, `limit` caps, ordering is newest-first, bounds are clamped server-side.
- **Events** are the source of truth; `RunView`/`TaskView`/`RunSummary` are
  projections. Anything the UI shows can be rebuilt from `/runs/{id}/events` via
  the canonical reducer (`fold_events`, ADR-0023).

## Stability policy

- **Additive within a major.** New routes, new optional fields, and new event
  types may appear in a minor release; existing routes, field names, and event
  semantics do not change.
- **Breaking changes** (removing/renaming a route or field, changing an event's
  meaning) require a **major** version bump and an ADR.
- **Version lockstep.** `GET /health.version` == backend `__version__` ==
  `pyproject` version == frontend `package.json` version, asserted in CI
  (`test_docs_parity.py`).
- **Event-stream compatibility** is an invariant (I-24): a ledger written by an
  older version replays identically under a newer one.
