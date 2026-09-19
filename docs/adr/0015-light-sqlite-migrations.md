# ADR-0015 — Light, idempotent SQLite column migrations

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
M4 extends the schema: a new `task_attempts` table and three new columns on `tasks`
(`attempt_count`, `replan_generation`, `parent_generation`). `Base.metadata.
create_all` creates a *new* table idempotently, but it **never alters an existing
table** — so on a database already created at v0.3.0, the new `tasks` columns would be
missing and the run would fail. A full migration framework (Alembic) is still deferred
until the Postgres move (§7 spec, ADR-0007); pulling it in now for three additive,
nullable/defaulted columns is disproportionate.

## Decision
Add a tiny, dependency-free, idempotent migration step in `Database.create_all`:
**`_apply_light_migrations(conn)`** reads `PRAGMA table_info(tasks)` and issues
`ALTER TABLE tasks ADD COLUMN …` only for columns that are missing. All three columns
are nullable or defaulted, so the back-fill is trivial and lossless. It runs after
`metadata.create_all` (which creates `task_attempts`), is SQLite-only, and is safe to
run on every startup — existing v0.3.0 databases upgrade in place with **no data loss
and no manual step**.

## Consequences
- Existing databases keep working across the v0.3.0 → v0.4.0 upgrade with zero
  operator action.
- Stays within the "defer speculative infrastructure" principle (ADR-0007): no Alembic
  dependency, no migration history table, until a real multi-environment/Postgres need
  exists.
- Trade-off: the mechanism only handles additive `ADD COLUMN` on SQLite — it cannot
  rename, drop, or backfill computed values. That is exactly M4's need; anything richer
  is the trigger to finally adopt Alembic.
