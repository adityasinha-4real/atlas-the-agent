# ATLAS technical-debt register

**Audit: technical debt (RFC-0004 §25).** Every known item is listed with a
disposition — **fixed in M6**, **accepted (with reason)**, or **deferred (tracked)**.
There is **no silent debt**: a sweep of `atlas/` for `TODO`/`FIXME`/`XXX` returns
nothing, and every `noqa`/`type: ignore` carries an inline reason.

## Suppressions (all deliberate, all reasoned)

| Kind | Where | Reason |
|---|---|---|
| `noqa: BLE001` | `obs/metrics.py`, `api/routes.py` (probes), `memory/{writer,service}.py`, `recovery/{integrity,reconciler}.py`, `persistence/database.py` (FTS fallback) | Best-effort boundaries: observability/probes/memory/recovery must **never** raise into a run (I-14, I-25). Each catch logs-and-degrades. |
| `noqa: ANN001` | `persistence/{database,repositories}.py` | Sync SQLAlchemy `Connection`/`AsyncSession` params where a precise annotation adds noise, not safety. |
| `noqa: ANN201` | `runtime/budget.py` | Signature intentionally mirrors `ToolRegistry.get`/`specs` (duck-typed wrapper). |
| `noqa: B027` | `llm/gateway.py` | `aclose` is an optional override hook, deliberately empty in the base. |
| `type: ignore[import-untyped]` | `tools/web_fetch.py` | `lxml.html` ships no stubs; import is guarded. |

## Register

| Item | Disposition | Notes |
|---|---|---|
| Finalize/backfill race (status flips to `done` a beat before the terminal event commits) | **Fixed (M6)** | Tests wait for the terminal **event** in the ledger, not the run status; documented in RFC §11/§19. Recovery/replay both key off events. |
| Hand-maintained `frontend/lib/types.ts` | **Resolved** | Now generated from the backend's OpenAPI schema (`frontend/lib/generated/schema.ts`, `make gen-api`); `lib/types.ts` aliases it. Both `openapi.json` and the generated client are committed. |
| Duplicated view-mapping (backend projections vs frontend reducer) | **Accepted / mitigated** | `fold_events` is the single **canonical reducer** (ADR-0023); golden-ledger fixtures assert equivalence, bounding drift. |
| No full migration tool (Alembic) | **Accepted** | Light additive/idempotent migrations suffice for SQLite (ADR-0015, I-27); Alembic deferred until Postgres (RFC §24). |
| Broad `except` outside best-effort boundaries | **None found** | Every broad catch is inside a documented best-effort seam; verified in the sweep. |
| Ruff rule coverage (`S`/`PTH`/`SIM`) | **Deferred (tracked)** | Current set `E,F,I,UP,B,ANN` is clean; adopting `S` (bandit) is a follow-up once false positives are triaged (see [SECURITY](SECURITY.md)). |
| `ollama`/`web_*` modules at low unit coverage | **Accepted** | Network I/O paths; arg-validation and error mapping are unit-tested, live calls are not run in CI (keeps CI model-free). Total coverage 92% clears the 85% floor. |

## Sweep evidence

- `grep -rn "TODO\|FIXME\|XXX" atlas/` → **0 matches**.
- Every `noqa`/`type: ignore` in the table above includes an inline justification.
- Coverage floor **85%** enforced in CI; measured **92%** at v0.6.0.
