# ATLAS — Final release checklist · v0.7.0

This is the pre-tag review for **`v0.7.0`** (Milestone M7: Ship). The release
process is in [docs/RELEASE.md](docs/RELEASE.md), and M7's scope and the demo
script are in [docs/milestones/M7.md](docs/milestones/M7.md).

**Date:** 2026-09-19 · **Tag:** `v0.7.0` (**not yet cut**; see *Blocked*) ·
**Scope:** packaging, CI and docs, plus one config-parsing fix. The agent
runtime is unchanged, and so are its defaults (I-13, I-15, I-23).

Each item is classified as follows:

- **COMPLETE**: verified by running it or inspecting it in this pass.
- **BLOCKED**: needs the owner.
- **DEFERRED**: explicitly outside M7.

---

## Gates (run in this pass)

| Gate | Command | Result | Status |
|---|---|---|---|
| Backend tests + coverage floor | `pytest --cov=atlas --cov-fail-under=85` | **312 passed**, **92.16%** | COMPLETE |
| Config regression tests | `pytest tests/test_config.py` | 26 passed; 8 fail against the pre-fix code | COMPLETE |
| Docs/version parity | `pytest tests/test_docs_parity.py` | passed (0.7.0 lockstep, `[0.7.0]` section, `.env.example` parity) | COMPLETE |
| Backend lint | `ruff check atlas tests` | clean | COMPLETE |
| Frontend type safety | `npm run typecheck` | clean | COMPLETE |
| Frontend build | `npm run build` | 5 routes (`/`, `/history`, `/history/[runId]`, `/memory`, `/_not-found`) | COMPLETE |
| Benchmarks (guard) | `python -m bench --ci` | all within thresholds | COMPLETE |
| Evals | `python -m evals` | 8/8 → [evals/scorecard.md](evals/scorecard.md) | COMPLETE |
| OpenAPI/client drift | `dump_openapi` + `gen:api`, then `git diff` | no drift | COMPLETE |
| CI workflow parses | `yaml` and `js-yaml` parse of `ci.yml` | valid (it was invalid in v0.6.0) | COMPLETE |
| Compose files | `docker compose config` (base and base+echo) | valid | COMPLETE |
| Docker images | `compose build backend frontend` | both built | COMPLETE |
| Container smoke (echo) | `compose -f … -f docker-compose.echo.yml up --build backend frontend` | `/health` 0.7.0, `/ready` 200, one run `done` (37 events), UI routes 200, CORS ok | COMPLETE |
| Frontend lint / unit tests | — | not configured (no ESLint, no test runner) | DEFERRED |
| Python type check (mypy) | — | config stub only; mypy not installed and not a CI gate | DEFERRED |

## Consistency (verified)

- **COMPLETE: version lockstep at `0.7.0`** in `atlas.__version__`,
  `pyproject.toml`, `package.json`/`package-lock.json`, `openapi.json` and
  `GET /health`.
- **COMPLETE: CHANGELOG** has a `[0.7.0]` section. `[Unreleased]` lists only
  future work.
- **COMPLETE: config parity.** Every `ATLAS_*` setting is in
  `backend/.env.example`, and there are no phantom variables (enforced by a
  test).
- **COMPLETE: TODO.** [docs/TODO.md](docs/TODO.md) marks History/Replay and the
  generated client as done (v0.6.0), shows M7 🟡 with its manual items open, and
  lists deferred work.
- **COMPLETE: README.** Every relative link resolves, and the Mermaid diagram
  passes the `mermaid` parser. Test and eval numbers match this pass.
- **COMPLETE: no stray `TODO`/`FIXME`/`XXX`** in `backend/atlas`,
  `frontend/{app,components,lib}`, `bench` or `evals`.
- **COMPLETE: design review §9 M7 items.** Packaging polish, CI badge, and the
  History page with replay are done. The demo GIF is BLOCKED (below). The eval
  scorecard is summarized and linked in the README; it can't be embedded in the
  demo until the GIF exists.

## Blocked: owner actions

1. **BLOCKED: demo GIF.** It needs Ollama with `qwen2.5:7b-instruct`, which
   isn't installed on this machine, to show a real multi-task plan and a
   visible retry. The README has a marked placeholder. The script is in
   [M7.md](docs/milestones/M7.md#demo-script-for-the-gif).
2. **BLOCKED: clean-machine smoke test.** Containers were built and
   smoke-tested on the dev machine, not a clean one. The Ollama compose path
   was only validated with `docker compose config`, not run.
3. **BLOCKED: publication and first CI run.** Remote
   `https://github.com/adityasinha-4real/atlas-the-agent` exists; so far its
   `main` holds only the README/license commit. The full tree still has to be
   pushed, and the first GitHub Actions run, which turns the badge green, has
   never happened. `master`'s older history carries co-author trailers the
   owner doesn't want published, so publish the tree as a new commit on
   `main`, not `master`.
4. **BLOCKED: tag `v0.7.0`.** [RELEASE.md](docs/RELEASE.md) step 9 comes after
   CI is green and the smoke test passes, so the tag is intentionally not cut.

## Deferred (outside M7)

Recall at execution/reflection time. A semantic memory tier. Postgres and
Alembic. `code_sandbox`, `PAUSED` and crash resume. Auth. Frontend
ESLint/tests. The `next@16` upgrade for two advisories. Backfilled tags
`v0.3.0`–`v0.5.0`: M3–M5 were never committed separately, so no commit
represents those versions, and tagging one would falsify history. Full list:
[docs/TODO.md](docs/TODO.md#deferred-post-m7) and
[known-limitations.md](docs/release/known-limitations.md).

---

## Sign-off

Every repository-level gate is green. The release is **ready to tag once the
four blocked owner actions are done**.
