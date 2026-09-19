# Release checklist — v0.6.0

Execution status of the release gates. The **process** (policy, versioning,
migration rules) lives in [../RELEASE.md](../RELEASE.md); this is the tick-list
for cutting `0.6.0`, verified this pass.

## Gates — all green

| Gate | Command | Result |
|---|---|---|
| Backend tests + coverage floor | `pytest --cov=atlas --cov-fail-under=85` | ✅ **307 passed**, coverage **92.15%** |
| Backend lint | `ruff check atlas tests` | ✅ All checks passed |
| Frontend type safety | `npm run typecheck` | ✅ Clean |
| Frontend build | `npm run build` | ✅ 5 routes (`/`, `/history`, `/history/[runId]`, `/memory`, `/_not-found`) |
| Benchmarks (guard) | `python -m bench --ci` | ✅ All within thresholds |
| Evals | `python -m evals` | ✅ **8/8** goldens pass |

## Consistency — verified

- ✅ **Version lockstep** `0.6.0`: `atlas.__version__` == `pyproject` ==
  `frontend/package.json` == `GET /health` (asserted by `test_docs_parity.py`).
- ✅ **CHANGELOG** has a `[0.6.0]` section.
- ✅ **Config parity** — every `ATLAS_*` setting documented in `.env.example`,
  no phantom vars (CI-enforced).
- ✅ **Make targets** referenced by the READMEs all exist: `setup`, `test`,
  `lint`, `build-frontend`, `gen-api`, `bench`, `bench-ci`, `evals`,
  `run-backend`, `run-frontend`, `up`, `down`.
- ✅ **CI** (`.github/workflows/ci.yml`) runs the same gates on a Windows + Linux
  matrix (backend) and Linux (frontend); hardened this pass with least-privilege
  `permissions: contents: read`.

## Remaining manual steps (owner action, not automatable here)

- ◻ **Smoke test** — clean-machine `docker compose up --build` reaches the UI;
  run one goal end-to-end.

> Note: v0.6.0 ships the M6 hardening work **and** the behavior-preserving
> frontend/DX work developed alongside it — the History/Replay page, the
> generated OpenAPI client, the accessibility/polish pass, and the finalized
> docs. All are folded into the `[0.6.0]` CHANGELOG section; none touch the agent
> runtime, which stays byte-for-byte M5 at defaults (I-23). See
> [FINAL_RELEASE_CHECKLIST.md](../../FINAL_RELEASE_CHECKLIST.md) for the cut.
