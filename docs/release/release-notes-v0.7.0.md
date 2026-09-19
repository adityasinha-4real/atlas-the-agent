# ATLAS v0.7.0 — release notes

**Milestone M7: Ship.** This release packages ATLAS for others to run and fixes
three bugs that affected fresh installs. The agent runtime (planning, ReAct
execution, reflection, memory and events) is unchanged from v0.6.0, and all
defaults are the same.

## Fixes that matter if you are upgrading

- **Startup crash with the shipped config.** A `.env` copied from `.env.example`,
  and the Docker backend, failed at startup. The cause was that
  `ATLAS_CORS_ORIGINS` / `ATLAS_MEMORY_WRITE_OUTCOMES` were JSON-decoded
  instead of split on commas. Comma-separated values now work, and JSON lists
  still do.
- **CI never ran.** The workflow file was invalid YAML. It is fixed.
- **Missing frontend config template.** `frontend/.env.local.example` is now in
  the repository.

## New

- **Run in Docker without a model:**
  `docker compose -f docker-compose.yml -f docker-compose.echo.yml up --build backend frontend`.
- **Contract drift is caught in CI.** Regenerating `openapi.json` or the typed
  client must produce no diff.
- **A new README** with an architecture diagram, a feature table, an eval
  summary and separate quick-start paths.

## Verified for this release

312 backend tests (92% coverage), ruff, frontend typecheck and build, benchmark
guard, evals 8/8, a Docker image build, and an echo-mode container smoke test.
Not yet verified: a clean-machine `docker compose up --build` with Ollama, and
the first GitHub Actions run.

## Known limitations

Unchanged from v0.6.0 apart from the resolved drift gate. See
[known-limitations.md](known-limitations.md).
