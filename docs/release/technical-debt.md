# Technical-debt register — v0.6.0

Snapshot of the debt posture at release. Full register with per-suppression
rationale: [../TECH_DEBT.md](../TECH_DEBT.md).

## Posture — ✅

- **No silent debt.** A sweep of `backend/atlas/` for `TODO`/`FIXME`/`XXX`
  returns nothing; every `noqa` / `type: ignore` carries an inline reason.
- **Coverage** measured **92%**, clearing the **85%** CI floor.
- Every broad `except` sits inside a documented best-effort boundary
  (observability, probes, memory, recovery) that must never raise into a run.

## Resolved since v0.5.0 / M6

- ✅ **Frontend types were hand-maintained** — now generated from the backend's
  OpenAPI schema (`frontend/lib/generated/schema.ts`, `make gen-api`), with
  `lib/types.ts` aliasing the generated schema. This was the one cross-cutting
  item previously tracked as deferred.

## Accepted / deferred (tracked, non-blocking)

| Item | Disposition |
|---|---|
| No full migration tool (Alembic) | **Accepted** — light additive/idempotent SQLite migrations suffice (ADR-0015); revisit for Postgres. |
| Ruff `S`/`PTH`/`SIM` rule sets not enabled | **Deferred** — current `E,F,I,UP,B,ANN` set is clean; adopt `S` (bandit) after triaging false positives. |
| `ollama` / `web_*` modules at low unit coverage | **Accepted** — live network I/O is not exercised in CI (keeps CI model-free); arg-validation and error mapping are unit-tested. |
| Duplicated view mapping (backend projections vs frontend reducer) | **Accepted / mitigated** — `fold_events` is the canonical reducer (ADR-0023); golden-ledger fixtures bound drift. |
| Frontend `next`/`postcss` advisories | **Deferred** — needs a `next@16` major bump; see [dependency-audit.md](dependency-audit.md). |
