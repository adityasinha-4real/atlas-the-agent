# ATLAS release process

**RFC-0004 §23-24.** Repeatable, auditable releases. Versions track milestones;
each release records its performance and quality posture.

## Versioning policy

- **Semver within the milestone cadence.** Additive API changes and
  behavior-preserving hardening are **minor** (M6 → `0.6.0`). Breaking changes
  (removed/renamed route or field, changed event semantics) gate a **major** bump
  and an ADR.
- **Lockstep.** Backend `atlas.__version__`, `backend/pyproject.toml`, frontend
  `package.json`, and `GET /health.version` are always equal — asserted by
  `test_docs_parity.py`, so a mismatch fails CI.
- **Precedence:** env var > `.env` > default. No version or config value is
  hardcoded anywhere else.

## Migration policy (formalizes ADR-0015)

All schema migrations are **additive and idempotent** — new tables via
`create_all`, new columns via guarded `ALTER TABLE … ADD COLUMN`, new FTS objects
via `CREATE … IF NOT EXISTS` + triggers. **No** drops, renames, or type changes.
Every migration is **downgrade-safe**: older code ignores unknown
tables/columns/rows (**I-27**). The startup reconciler reads only columns
guaranteed present, so a mixed-version start is safe. Full Alembic remains
deferred until Postgres is on the horizon.

M6's additions — the `schema_meta` version row and the `ix_runs_created` index —
are both additive and tested against a v0.5.0-shaped database.

## Release checklist

1. **Green tree.** `make test` (pytest + coverage floor), `make lint` (ruff +
   frontend typecheck), `make build-frontend` all pass locally.
2. **Bump versions in lockstep** — `atlas/__init__.py`, `backend/pyproject.toml`,
   `frontend/package.json`. (`test_docs_parity.py` enforces equality.)
3. **CHANGELOG.** Add the `[x.y.z] — <date> · Milestone <M>` section; link the RFC
   and the ADRs; note migrations.
4. **Benchmarks.** `make bench` → commit updated `bench/BASELINE.md`; paste p50/p95
   and RSS deltas into the CHANGELOG/milestone note.
5. **Evals.** `make evals` → commit `evals/scorecard.md` + `results.json`; confirm
   pass-rate.
6. **Docs.** Update README status/roadmap/test-count, ARCHITECTURE, the milestone
   doc (`docs/milestones/Mx.md`), TODO, and the ADR index. `test_docs_parity.py`
   guards version/config parity.
7. **CI green** on the full matrix (Windows + Linux): lint, tests+coverage,
   `bench-ci`, `evals`, frontend build.
8. **Smoke.** Clean-machine `docker compose up --build` reaches the UI; run one
   goal end-to-end.
9. **Tag** `vX.Y.Z`.

## Provenance

Each version's CHANGELOG entry links its RFC/ADRs and carries the bench/eval
deltas, so the performance and quality posture of every release is on record.
