# Documentation audit — v0.6.0

**Scope:** doc/code parity across README, backend/frontend READMEs, and the
`docs/` set. Automated parity is enforced by
`backend/tests/test_docs_parity.py`; full checklist in
[../DOC_AUDIT.md](../DOC_AUDIT.md).

## Automated (CI-enforced) — ✅

- Version consistent: `atlas.__version__` == `pyproject` == frontend
  `package.json` == `GET /health` == `0.6.0`.
- Every `ATLAS_*` setting documented in `.env.example`; no phantom vars.
- CHANGELOG has a `[0.6.0]` section.
- OpenAPI documents the expected route set.

## Corrected this pass (documentation only)

- ⚠️ [../API.md](../API.md): `POST /runs/{id}/cancel` response corrected from
  `RunView` to the actual `202 {run_id, cancel_requested}`; removed the phantom
  `409` from the error conventions (no route emits `409`). See
  [api-consistency.md](api-consistency.md).
- ⚠️ [../TECH_DEBT.md](../TECH_DEBT.md): the "hand-maintained
  `frontend/lib/types.ts`" item is now **Resolved** — the types are generated
  from the OpenAPI schema. See [technical-debt.md](technical-debt.md).
- ⚠️ Root/backend/frontend READMEs were rewritten to describe the **current**
  repository (History/Replay + Memory pages, generated client, live commands),
  with milestone-status framing removed.

## Verified links & structure — ✅

- Every doc referenced from the root README exists (`docs/ARCHITECTURE.md`,
  `API.md`, `SECURITY.md`, `RELEASE.md`, `TECH_DEBT.md`, `DOC_AUDIT.md`,
  `TODO.md`, `bench/README.md`, `evals/README.md`, `backend/.env.example`).
- ADR index (`docs/adr/README.md`) lists ADR-0001…0024, all present.
- README `make` commands all resolve to real Makefile targets (see
  [release-checklist.md](release-checklist.md)).

## Known cosmetic (not corrected)

- ◽ `frontend/package.json` `description` still reads "M6: hardened runtime". A
  manifest description, not user-facing docs; left unchanged to avoid manifest
  churn during a behavior-preserving pass.
