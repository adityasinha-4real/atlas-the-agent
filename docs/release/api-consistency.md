# API consistency audit — v0.6.0

**Scope:** all 13 routes in `backend/atlas/api/routes.py` + the WebSocket stream
(`api/ws.py`), verified against the running OpenAPI schema
(`frontend/openapi.json`). Full reference: [../API.md](../API.md).

## Findings

- ✅ **Typed responses.** Every route that returns domain data declares an
  explicit Pydantic `response_model` (`HealthView`, `ToolSpec`, `RunView`,
  `RunSummary`, `TaskView`, `Event`, `MemoryView`). No bare dicts on typed
  routes.
- ✅ **Intentionally untyped routes** are consistent and documented: `/ready`
  (custom `200`/`503` JSON probe), `/metrics` (Prometheus text or `404`), and
  `/runs/{id}/cancel` (`202` acknowledgement dict). These are excluded from the
  generated client by design and match their route implementations.
- ✅ **Status codes** are consistent across the surface: `200` reads, `201`
  create-run, `202` cancel, `204` memory delete, `404` unknown id / disabled
  metrics, `503` not-ready. No `500` is expected on any path.
- ✅ **Path-set guard.** `test_api_memory.py::test_openapi_documents_memory_routes`
  fails CI on accidental route additions/removals; version parity is asserted by
  `test_docs_parity.py`.
- ✅ **Client generated from the contract.** The frontend REST client is
  generated from this schema (`make gen-api`), so a backend contract change
  surfaces as a frontend type error, not silent drift.

## Corrected this pass (documentation only)

- ⚠️ [../API.md](../API.md) listed `POST /runs/{id}/cancel` as returning
  `RunView`. The implementation returns **`202`** with
  `{run_id, cancel_requested}`. Corrected.
- ⚠️ [../API.md](../API.md) conventions listed a `409` "illegal state
  transition". No route emits `409` — cancel is idempotent and returns `202`
  with `cancel_requested=false` on terminal runs. Corrected.

## Recommendation (non-blocking)

- ✅ **Schema-drift CI guard** *(landed in v0.7.0)*. CI now regenerates and
  diffs both halves of the contract: the backend job re-dumps `openapi.json` and
  the frontend job re-runs `npm run gen:api`, each failing on `git diff
  --exit-code`. Splitting the check across the two jobs avoids needing Python
  and Node in one job.
