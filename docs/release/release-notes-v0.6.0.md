# ATLAS v0.6.0 — release notes

**2026-07-10 · Milestone M6: Hardening.** The runtime is now **measured,
observable, recoverable, and audited** — with **zero behavior change at
defaults**. Every subsystem added here is off, passive, or a no-op unless
explicitly enabled, so a default install behaves byte-for-byte like v0.5.0
(invariant I-23, enforced by a parity test). No new agent capabilities were
added; this release hardens what v0.1.0–v0.5.0 shipped.

Full detail: [CHANGELOG](../../CHANGELOG.md) · design
[RFC-0004](../rfc/0004-m6-hardening.md).

## Major additions since v0.5.0

- **Deterministic benchmark suite** (`bench/`) — offline, `echo`/scripted-driven
  latency, RSS, SQLite query-plan, FTS, and event-stream measurements with
  committed baselines and a CI threshold guard (`make bench` / `make bench-ci`).
- **Passive observability** (`atlas/obs/`) — a dependency-free in-process metrics
  registry (Prometheus text), `GET /metrics` (404 when disabled), a `GET /ready`
  dependency probe, and opt-in structured JSON logging with run/task correlation
  ids. Best-effort — recording can never raise into a run.
- **Crash recovery** (`atlas/recovery/`) — a startup reconciler brings runs left
  non-terminal by a crash to a consistent terminal state derived solely from the
  event ledger; idempotent, on by default, a no-op on a clean DB.
- **Replay verification** — `fold_events`, the canonical ledger→state reducer,
  shared by the reconciler and tests, with golden-ledger fixtures and a sequence
  integrity check.
- **Evaluation harness** (`evals/`) — 8 goldens (single/multi-task, tool-use,
  retry, replan, partial, failure, memory-lift) run through the real runtime on a
  scripted gateway, scored into a committed `scorecard.md` (`make evals`).
- **Integrity & determinism** — optional startup `PRAGMA quick_check` + FTS-drift
  check, a `schema_meta` version row, and a repeated-run determinism test.
- **CI** — `.github/workflows/ci.yml`: Windows + Linux matrix, tests + coverage
  floor, ruff, doc/config parity as a test, `bench-ci`, `evals`, and frontend
  typecheck + build.
- **Audit reports** — `docs/{API,SECURITY,RELEASE,TECH_DEBT,DOC_AUDIT}.md`.
- **Storage** — an `ix_runs_created` index (list pagination now index-scanned,
  ~60% faster p50/p95) and `PRAGMA temp_store=MEMORY`; benchmark-justified, no
  observable behavior change.
- **History & replay page** — a `/history` list of past runs plus a read-only,
  scrubber-style replay that re-folds a finished run's ledger through the same
  `runReducer` the live page uses (live and replay share one code path).
- **OpenAPI-generated frontend client** — the typed client and `lib/types.ts` are
  now generated from the backend's OpenAPI schema (`make gen-api`), so a contract
  change surfaces as a frontend type error. Plus an accessibility/polish pass and
  a finalized doc set. All behavior-preserving — no agent runtime change.

## Configuration added (all preserve v0.5.0 behavior at defaults)

`ATLAS_METRICS_ENABLED` (false), `ATLAS_LOG_FORMAT` (text),
`ATLAS_RECOVERY_ENABLED` (true — only affects interrupted runs),
`ATLAS_DB_INTEGRITY_CHECK` (false).

## Migration

Additive and idempotent (I-27): first start on a v0.5.0 database creates the
`schema_meta` row and the `ix_runs_created` index — no manual step, no data loss,
no existing table altered, downgrade-safe. Full Alembic remains deferred until
Postgres.

## Verification (this release)

| Gate | Result |
|---|---|
| Backend tests | 307 passed, coverage 92.15% (floor 85%) |
| Lint (ruff) | clean |
| Frontend typecheck / build | clean · 5 routes |
| Benchmarks (`--ci`) | all within thresholds |
| Evals | 8/8 pass |

## Known limitations

Local single-user posture (no auth), local model needed for the full multi-task
flow, keyword (not semantic) memory recall, and two pending frontend dependency
advisories. Full list: [known-limitations.md](known-limitations.md).
