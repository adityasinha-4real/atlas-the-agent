# ATLAS Frontend

Next.js 14 (App Router) · TypeScript · Tailwind CSS. Implements the **Run page**
(design doc §1.7) — the primary demo surface — plus **History/Replay** and
**Memory** pages. The Run page renders a **plan checklist** that ticks each
task `pending → running → done` live, above the event feed (thoughts, tool
calls, task events) and the streaming synthesized answer.

## Setup & run

```bash
npm install
cp .env.local.example .env.local   # set NEXT_PUBLIC_API_BASE if not localhost:8000
npm run dev                        # http://localhost:3000
```

The backend must be running (see `../backend/README.md`). Start it with the
`echo` provider for a model-free demo.

## Scripts

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server with HMR |
| `npm run build` | Production build |
| `npm run start` | Serve the production build |
| `npm run typecheck` | `tsc --noEmit` (the type-safety gate; CI runs this) |
| `npm run gen:api` | Regenerate `lib/generated/schema.ts` from `openapi.json` |

## Structure

| Path | Purpose |
|---|---|
| `app/` | App Router entry (Run, `/memory`, `/history`, `/history/[runId]`) |
| `components/RunPage.tsx` | Goal form, status, plan checklist, answer, event feed |
| `components/RunHistory.tsx` | Past-run list (`GET /runs`) |
| `components/RunReplay.tsx` | Read-only replay of a finished run, with a scrubber |
| `components/PlanChecklist.tsx` | Event-sourced task checklist (ADR-0009) |
| `components/EventFeed.tsx`, `StatusBadge.tsx` | Presentational pieces |
| `lib/api.ts` | Typed backend client (openapi-fetch) + WebSocket helpers |
| `lib/runReducer.ts` | Pure event→state fold, shared by live stream and replay |
| `lib/types.ts` | Domain types, aliased from the generated schema |
| `lib/generated/schema.ts` | **Auto-generated** OpenAPI types — do not edit |
| `lib/useRunStream.ts` | WebSocket hook: stream, reconstruct answer, reconnect |

## API client (generated)

REST calls go through an [openapi-fetch](https://openapi-ts.dev) client bound to
types generated from the backend's OpenAPI schema, so paths, query params, and
response shapes are checked against the real contract. `lib/types.ts` aliases the
generated `components["schemas"]` — a backend contract change surfaces as a
frontend type error, not silent drift.

Regenerate after any backend route/schema change (or run `make gen-api` from the
repo root):

```bash
cd backend && .venv/Scripts/python.exe -m scripts.dump_openapi  # → frontend/openapi.json
cd frontend && npm run gen:api                                  # → lib/generated/schema.ts
```

Both `openapi.json` and `lib/generated/schema.ts` are committed so the build is
reproducible without a running backend. Only `lib/generated/` is generated;
everything else in `lib/` is hand-written.

## Streaming & replay

`useRunStream` opens `WS /runs/{id}/stream`, applies each event to local state
via `runReducer`, and — on an unexpected disconnect — reconnects with
`?after=<lastSeq>` so no event is missed or duplicated. The **History** page
(`/history`) reuses that *same* reducer to fold a finished run's events back into
the exact view that streamed live — replay for free from the ledger (ADR-0009).
WebSockets are not modeled by OpenAPI, so these helpers stay hand-written.
