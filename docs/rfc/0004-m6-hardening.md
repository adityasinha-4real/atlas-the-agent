# RFC 0004 — Milestone M6: Hardening

- **Status:** **Proposed** (2026-07-10) — design only; no production code in this RFC.
- **Author:** ATLAS engineering
- **Date:** 2026-07-10
- **Builds on:** M5 episodic memory (v0.5.0), [RFC-0003](0003-m5-episodic-memory.md)
- **Spec basis:** `ATLAS-Design-Review-V2.md` §7 (operability), §9 (M6 row);
  [ADR-0005](../adr/0005-emit-over-event-bus.md) (ledger), [ADR-0015](../adr/0015-light-sqlite-migrations.md)
  (migrations), [ADR-0012](../adr/0012-central-budget-enforcement.md) (budgets)
- **Target version:** **0.6.0**

---

## 0. TL;DR

Through M5 the system grew in capability (plan → act → reflect → recover →
remember). **M6 adds no capability.** It makes what already exists *fast,
measurable, recoverable, and provably correct*. The work is: (1) a **deterministic
benchmark + profiling harness** that pins latency/throughput/memory baselines and
guards them in CI; (2) **storage/query/FTS/event-stream optimization** driven by
those measurements, not guesswork; (3) **crash recovery** that reconciles an
interrupted run's state from the event ledger on startup, plus **replay
verification** and a **determinism harness** that prove the ledger is the single
source of truth; (4) **passive observability** — metrics, structured logging, and
health/readiness signals that never mutate a run; and (5) a set of **audits**
(API, docs, security, config, tech debt) and **process** hardening (CI, release,
migration policy). The M5 eval harness deferred in [TODO](../TODO.md) lands here.

Every change is **additive and behavior-preserving**: with all new configuration
at its defaults, a run emits the **same events in the same order** and produces the
**same output** as v0.5.0 (invariant **I-23**). Observability is best-effort and
can never raise into a run (**I-25**). No new agent tools, no new run states, no
new planning/reflection/memory behavior. Two guarded schema additions
(`run_checkpoints` view is derived, not stored; an optional `metrics` counter
table), both additive and downgrade-safe (**I-27**).

---

## 1. Objectives

**Objective.** Turn ATLAS from *feature-complete* into *operationally trustworthy*:
known performance envelopes, recoverable from a crash, provably deterministic on
replay, observable in production, and audited for correctness, security, and debt —
without altering a single unit of user-visible behavior.

**Success criteria (measurable).**
1. **Baselines exist and are enforced.** A committed `bench/` suite reports p50/p95
   latency, throughput, and peak RSS for the canonical flows (plan, single ReAct
   task, full multi-task run, recall, write) against the deterministic providers;
   CI fails on a regression beyond a configured threshold.
2. **Crash recovery works.** A run interrupted (process kill) mid-`RUNNING` is
   reconciled on next startup to a terminal, consistent state derived **solely**
   from its persisted ledger + rows — no orphaned `RUNNING` rows, no fabricated
   events (I-26).
3. **Replay is verified.** A golden-ledger test proves that replaying a run's
   `events` reconstructs byte-identical derived state (plan checklist, task views,
   answer) — the ledger is provably authoritative (I-24).
4. **Determinism is guarded.** The same seed + scripted inputs produce identical
   event sequences across runs and platforms; a determinism test asserts it.
5. **Observability is live and passive.** `/metrics` (text exposition) and
   `/health` + `/ready` expose counters/latencies and dependency status; enabling
   them changes no run behavior and cannot fail a run (I-23, I-25).
6. **Optimizations are proven, not assumed.** Each SQLite/FTS/query/event change is
   accompanied by a before/after benchmark delta in the PR and the CHANGELOG.
7. **Evals exist.** 8–12 golden goals run offline (FakeLLM) → `scorecard.md` with
   pass-rate, step-count, and token-budget metrics; committed and reproducible.
8. **Audits are documented.** API-consistency, documentation, security, config, and
   tech-debt audits each produce a checked-in report with resolved/accepted items.
9. **Parity holds.** M1–M5 tests stay green; with defaults, byte-for-byte M5
   behavior (I-23). Target **~30–40 new tests** (unit + bench-smoke + recovery +
   replay + evals).

---

## 2. Non-goals

- **No new agent capabilities.** No `code_sandbox` tool, no new tools of any kind,
  no `PAUSED` state, no approval seam, no new planning/reflection/memory features.
  These remain future milestones (M7+), explicitly out of scope here.
- **No feature expansion.** No new run states, no new events on the default path,
  no new user-facing surfaces beyond read-only ops endpoints (`/metrics`,
  `/ready`) and the (already-deferred) eval scorecard.
- **No behavior change.** Recovery, metrics, and optimization must not alter the
  events a run emits, their order, or the produced answer at default config.
- **No new heavy dependencies.** No Prometheus client lib, no APM agent, no vector
  DB, no message bus. Metrics use a tiny in-process registry and a text exposition
  format; profiling uses the stdlib (`cProfile`, `tracemalloc`, `time.perf_counter`).
- **No horizontal scale / multi-instance.** Single-process `EventHub` and
  single-file SQLite remain (the concurrency review §11 *documents* the ceiling and
  hardens within it; it does not lift it).
- **No Postgres / Alembic migration.** The light-migration policy (ADR-0015)
  continues; full migrations stay deferred (§24).
- **No auth/multi-tenant model.** The security review (§26) hardens the existing
  single-user local posture; it does not add authentication.

---

## 3. Architecture impact

M6 is **cross-cutting but additive**. It introduces two new *passive* modules and
touches wiring only at seams that already exist.

```
            ┌──────────────── NEW (passive, opt-in, best-effort) ────────────────┐
            │  atlas/obs/         metrics registry · timers · structured logging │
            │  atlas/recovery/    startup reconciler (ledger → terminal state)   │
            │  bench/             deterministic benchmark + profiling harness     │
            │  evals/             golden goals → scorecard                        │
            └────────────────────────────────────────────────────────────────────┘
 REST/WS ── unchanged contracts ──►  + GET /metrics, GET /ready (additive)
 RunManager ── unchanged flow ──►     wrapped by obs timers (no-op when disabled)
 emit()/EventHub ── unchanged ──►     obs counts events; recovery reads them back
 Repositories/Database ── unchanged schema ──► + tuned pragmas/indexes, reconciler query
```

- **New, self-contained:** `atlas/obs/` (metrics + logging), `atlas/recovery/`
  (startup reconciliation), top-level `bench/` and `evals/` harnesses. None are on
  a run's hot path unless explicitly enabled; all are best-effort.
- **Touched at seams only:** `emit()` gains an optional metrics hook (§30);
  `Database.create_all` gains a reconciliation pass and pragma/index tuning (§7,
  §13); `create_app` mounts `/metrics` and `/ready` and runs the reconciler in
  lifespan startup; `RunManager` phase boundaries are wrapped by timers that are
  no-ops when observability is off.
- **Untouched:** Planner, Executor, Reflector, Synthesizer, MemoryStore, tool
  registry, the run/task FSMs, all event types, all existing routes and their
  response schemas.

The guiding rule: **M6 code observes and recovers; it never decides.** No M6
component may change what the agent does — only measure it, persist enough to
reconstruct it, and report it.

---

## 4. Performance profiling

**Goal.** Attribute wall-clock and allocation to layers so optimization targets
data, not intuition.

- **Harness:** `bench/profile.py` runs canonical flows under the deterministic
  providers (`echo` + `ScriptedGateway`) with `cProfile` (call/cumulative time) and
  `tracemalloc` (allocation snapshots), writing `bench/out/profile-*.txt` and a
  compact top-N summary. LLM latency is *excluded* by construction (deterministic
  providers return instantly), isolating **framework overhead** — serialization,
  DB round-trips, event fan-out, context building, ranking.
- **Scenarios profiled:** planner prompt build; one ReAct iteration (envelope parse
  + repair loop + tool dispatch); context builder over N prior outputs;
  `emit()` + WS fan-out at K subscribers; memory recall (FTS query + ranking) and
  write (distill heuristic + upsert + prune).
- **Method:** warm-up run discarded; ≥ 20 iterations; report cumulative time by
  module and top allocators. Profiling is **offline/manual + CI-smoke** (one short
  scenario in CI to catch pathological regressions), never in the request path.
- **Deliverable:** `bench/PROFILE.md` — a baseline table (function → % cumulative,
  peak KiB) checked in at M6 start and updated as optimizations land.

---

## 5. Latency benchmarks

**Goal.** Pin per-operation latency envelopes and guard them.

- **Harness:** `bench/latency.py` measures p50/p95/p99 over the deterministic path
  using `time.perf_counter`, for: `POST /runs` → first event; plan produced;
  per-task turnaround; run → terminal; `GET /runs/{id}/events` (cold/warm);
  `GET /memories?q=` (FTS vs LIKE fallback); WS subscribe → first backfilled event.
- **Budgets:** each metric has a committed threshold in `bench/thresholds.json`
  (e.g. framework overhead per ReAct iteration < X ms on the CI runner). CI runs a
  **short** subset and fails on breach beyond a tolerance band (default ±25 % to
  absorb runner noise; configurable).
- **Reporting:** `bench/latency.py --report` emits a markdown table into
  `bench/out/latency.md` and a machine-readable JSON for trend tracking.
- **Explicitly measured, not optimized-for:** real-model latency (dominated by
  Ollama) is out of scope — M6 measures and bounds *our* overhead, and records the
  model contribution separately for context.

---

## 6. Memory profiling

**Goal.** Bound process memory and detect leaks across long-lived sessions.

- **Peak RSS** per scenario via `tracemalloc` + `resource`/`psutil`-free stdlib
  sampling (a lightweight RSS reader; no new dependency — `ctypes`/`/proc`-free via
  the platform API already available, else skipped gracefully).
- **Leak detection:** run 500 sequential runs under `echo`; assert steady-state RSS
  and that per-run retained objects return to baseline (tracemalloc diff between
  snapshots ≈ 0 growth). Targets the known suspects: the in-memory `EventHub`
  subscriber set, per-run `_lessons`/budget dicts (must be popped in `_cleanup`),
  and any accumulating caches added in M6.
- **Bounded-buffer audit:** every M6 buffer (metrics registry, WS queues, benchmark
  accumulators) is asserted bounded (I-28). The metrics registry is fixed-cardinality
  (no per-run labels) to prevent unbounded label growth.
- **Deliverable:** `bench/MEMORY.md` baseline + a `test_no_leak_over_n_runs`
  regression test (generous threshold, non-flaky).

---

## 7. SQLite optimization

**Goal.** Make the single-file store fast and contention-free within the
single-writer model — no schema/behavior change.

- **Pragmas (verify/standardize):** `journal_mode=WAL`, `synchronous=NORMAL`,
  `foreign_keys=ON` (existing); add `busy_timeout` (e.g. 5000 ms) so transient write
  locks retry instead of erroring under WS-driven read bursts; consider
  `cache_size` and `mmap_size` tuning validated by §5 benchmarks. `temp_store=MEMORY`
  for sort/FTS temp tables. All applied centrally in `Database` engine setup.
- **Connection strategy:** confirm the async engine pool sizing for aiosqlite; a
  single logical writer (SQLite constraint) with WAL readers. Document the model in
  ADR-0024 (§11).
- **Index review:** verify covering indexes for the hot queries — `events` by
  `(run_id, seq)` (ordering/backfill), `tasks` by `(run_id, …)`, `memories` by
  `(status, created_at)` and the pagination order `(created_at desc, id desc)`.
  Add only indexes that a benchmark shows helps; each addition is a guarded,
  additive migration (ADR-0015).
- **Write batching:** `emit()` is one insert per event today; measure whether
  batching event writes within a task boundary reduces fsync pressure **without**
  changing ordering or the streaming contract. Only adopt if benchmarks justify and
  ordering guarantees are preserved (I-24).
- **VACUUM/retention:** document a manual/opt-in maintenance path (bounded by
  memory prune already; events retention is out of scope but noted).

---

## 8. Event stream optimization

**Goal.** Lower fan-out cost and backfill latency without touching the
subscribe-then-backfill contract (M1) or `seq` monotonicity.

- **Backfill query:** ensure `GET /runs/{id}/events?after=seq` uses the
  `(run_id, seq)` index and streams rather than materializing large lists; add
  keyset pagination if a benchmark shows large-ledger backfill is slow.
- **WS fan-out:** review `EventHub` publish — bounded per-subscriber queues with a
  drop-oldest-or-disconnect policy for a stalled client (documented), so one slow
  consumer can't grow memory unboundedly (I-28). No change to delivered ordering
  for healthy consumers.
- **Serialization:** measure event JSON serialization cost; reuse a single
  serializer / precomputed payloads where an event is delivered to K subscribers
  (serialize once, send many).
- **Invariant guard:** a test asserts that under load the emitted `seq` sequence per
  run is still gapless and monotonic and that backfill+live delivery equals the
  persisted ledger (I-24).

---

## 9. Query optimization

**Goal.** Eliminate N+1s and redundant reads on the run/task/event/memory read
paths.

- **Audit** the repository methods behind `GET /runs/{id}`, `/tasks`, `/events`,
  `/memories` for per-row follow-up queries; batch with `IN`/joins where found.
- **EXPLAIN QUERY PLAN** captured for each hot query into `bench/QUERYPLANS.md`;
  any full-table scan on a hot path is either indexed away or justified.
- **Pagination** everywhere unbounded lists can grow (events, memories) — confirm
  limits/offsets or keyset; cap default page sizes.
- **View construction** (`to_view` mappers) profiled for redundant serialization;
  no behavior change, only fewer/cheaper reads.

---

## 10. FTS optimization

**Goal.** Keep recall fast and correct as `memories` grows to the bounded max.

- **bm25 weighting:** expose/validate column weights (`goal`,`summary`,`lessons`) so
  relevance matches intent; keep the Python re-rank (salience/recency/outcome) but
  ensure the FTS candidate set is the right size (`memory_recall_k` × a small
  fan-out, not the whole table).
- **`optimize`/`rebuild`:** add an opt-in `INSERT INTO memories_fts(memories_fts)
  VALUES('optimize')` maintenance step after bulk prune to keep the index compact;
  document trigger-sync integrity (ADR-0017) is preserved.
- **Fallback parity:** benchmark the `LIKE` fallback vs FTS5 at max records; assert
  the fallback stays correct (same candidate membership, weaker ranking) and
  bounded, and log which path is active.
- **Query hardening:** confirm FTS `MATCH` query construction escapes/handles
  special syntax (existing `_fts_match_query`) so a user `?q=` can't error the
  endpoint — a fuzz test over query strings (§26).

---

## 11. Concurrency review

**Goal.** Document and harden the concurrency model to its correct single-process
ceiling; remove races within it.

- **Model (to be recorded as ADR-0024):** one SQLite writer (WAL), many readers;
  each run is orchestrated by a single asyncio task; cross-run isolation is by
  `run_id`. The in-memory `EventHub` is single-process (known limitation).
- **Race audit:** the known finalize-vs-backfill timing (`test_events_after_cursor`
  flakiness) — reconcile by ensuring terminal-state write and its terminal event
  are ordered/committed before the run is observable as terminal, or by documenting
  the read-your-writes expectation. Audit memory write racing `run.completed`
  (already handled in tests via wait-for-count) and ensure the API reflects a
  consistent snapshot.
- **Cancellation:** re-verify cooperative cancel points (planning, between tasks,
  between ReAct iters, around reflection/replan, between chunks) still hold; add a
  test that a cancel during memory write leaves a consistent store.
- **Locking:** `busy_timeout` (§7) plus confirming no long-held write transaction
  spans an LLM call (transactions must not wrap model I/O). Audit for that pattern.

---

## 12. Failure recovery

**Goal.** Make every failure mode degrade to a defined, tested state — extend the
M4 containment story to infrastructure faults.

- **Taxonomy:** classify failures — model/provider error, tool error, DB error,
  serialization error, event-emit error, memory error — and assert each maps to a
  defined outcome (task observation, graceful abort, best-effort skip, or run
  failure) with a corresponding event. No failure may hang a run or leave it
  non-terminal (except a true crash, handled by §13).
- **Best-effort boundaries** (already: memory I-14): confirm metrics/logging (§25)
  and any M6 addition follow the same "catch, log, continue" rule.
- **Provider outage:** if Ollama is unreachable, a run fails cleanly with a typed
  event and message (no PAUSED state in M6 — that's future); the failure-injection
  suite (§19) covers it.
- **Idempotent finalizers:** memory write is already write-once (I-17); audit all
  finalizers for idempotency so a retried/reconciled finalize can't double-emit.

---

## 13. Crash recovery

**Goal.** After an abrupt process termination, bring interrupted runs to a
consistent terminal state on next startup, derived only from persisted data
(**I-26**) — no new run state, no fabricated history.

- **Reconciler** (`atlas/recovery/reconciler.py`, run in lifespan startup): scan for
  runs in a non-terminal state (`CREATED`/`PLANNING`/`RUNNING`) with no live
  orchestrating task (always true after restart). For each, reconstruct progress
  from the ledger + `tasks` rows and move it to a terminal state:
  - If a synthesized answer/`run.completed` exists → mark `DONE`/`FAILED`
    consistent with the ledger (reconcile the row to what the events already say).
  - Otherwise → mark `FAILED` with a typed `run.failed` reason
    `interrupted_by_shutdown`, preserving any partial task results already
    persisted. This is a **recovery event appended at reconcile time**, clearly
    attributed, not a fabricated mid-run event (I-26).
- **What it does NOT do (M6 scope):** it does **not** resume execution (no
  re-planning, no continuing the ReAct loop) — resumability is a larger design left
  to a future milestone. M6 guarantees **consistency and no orphaned RUNNING rows**,
  not continuation. (Recorded in ADR-0021: *reconcile-to-terminal, not resume*.)
- **Determinism:** reconciliation is a pure function of persisted state; running it
  twice is idempotent (second pass finds only terminal runs).
- **Tests:** simulate a crash by persisting a run mid-flight (rows/events without a
  terminal marker), start a fresh app, assert the run is terminal + a single
  attributed recovery event + no data loss.

---

## 14. Persistence integrity

**Goal.** Guarantee the store is internally consistent and detect corruption early.

- **Startup integrity check (opt-in/fast):** `PRAGMA quick_check` at startup behind
  a config flag; on failure, log loudly and refuse to serve rather than corrupt
  further (fail-fast). Default keeps startup fast.
- **Referential integrity:** `foreign_keys=ON` verified; assert FK/`ON DELETE`
  semantics (memory `run_id` SET NULL) hold under a run-deletion test.
- **FTS/content sync:** a consistency check comparing `memories` rowids to
  `memories_fts` (trigger drift detection) with an opt-in `rebuild`; a test that
  after insert/update/delete the index matches the content (ADR-0017).
- **Schema/version stamp:** record an internal `schema_version`/app-version marker
  (a tiny `meta` row) so migrations and recovery can reason about on-disk shape;
  additive and guarded (I-27).
- **Write-atomicity:** confirm multi-statement finalizes commit atomically so a
  crash leaves either the pre- or post-state, never a torn one.

---

## 15. Replay verification

**Goal.** Prove the ledger is authoritative — derived state is a pure function of
events (**I-24**).

- **Golden-ledger corpus:** capture the event ledgers of a set of canonical runs
  (plan, retry, replan, partial abort, recall+write) as fixtures in
  `evals/golden_ledgers/`.
- **Replay engine (test-only):** a pure reducer that folds a ledger into derived
  views (run status, plan checklist, task views, answer) — mirroring the frontend's
  event-sourced reducer and the backend view builders — and asserts it equals the
  persisted views. This makes "the UI can rebuild everything from events" a checked
  invariant, not a claim.
- **Round-trip test:** run → persist → drop derived caches → rebuild from `events`
  → assert identical. Guards against any code path that mutates state outside the
  ledger.
- **Cross-check:** the frontend reducer (`useRunStream`) and backend reducer agree
  on the same golden ledgers (shared fixtures), preventing drift between the two
  event interpreters.

---

## 16. Deterministic execution

**Goal.** Same inputs ⇒ same event stream, everywhere (**I-24**).

- **Sources of nondeterminism audited:** wall-clock timestamps, `id` generation,
  dict/set ordering, async scheduling interleavings, and any `random`. Ensure IDs
  and timestamps are the *only* nondeterministic fields and that they are excluded
  from equivalence comparisons (compare structure + ordering, not raw ids/times).
- **Seeded scripted path:** the `ScriptedGateway`/FakeLLM path is fully
  deterministic; add a determinism test that runs the same scenario twice and
  asserts identical event *types/order/payloads* (modulo id/timestamp).
- **Ranking determinism:** memory ranking already has deterministic tie-breaks;
  assert stable ordering under equal scores across platforms.
- **Cross-platform:** the suite runs on the CI matrix (§22) to catch
  ordering/locale/float-format divergence (Windows/Linux).

---

## 17. API consistency audit

**Goal.** One coherent, documented, versioned REST surface — no drift.

- **Contract sweep:** every route's response model is an explicit Pydantic schema
  (no bare dicts); error responses use a consistent shape and status codes
  (404/422/409/500) across `runs`, `tasks`, `events`, `tools`, `memories`.
- **OpenAPI as source of truth:** assert `/openapi.json` documents every route,
  every model, and that examples/descriptions exist; a test snapshots the path set
  so accidental additions/removals are caught (extends the existing
  `test_openapi_documents_memory_routes`).
- **Versioning:** `/health.version` == package version == frontend version, asserted
  (extends `test_health_reports_v050` → `v060`). Document the API-stability policy
  (additive-only within a major; breaking changes require a version bump + note).
- **Pagination/filtering consistency:** `limit`/`offset` semantics, ordering, and
  bounds identical across list endpoints; documented once.
- **Deliverable:** `docs/API.md` (or an ARCHITECTURE §) enumerating the frozen
  surface + the stability policy.

---

## 18. Documentation audit

**Goal.** Docs match code exactly — no stale counts, versions, or claims.

- **Automated checks (CI):** a doc-lint step asserting the test-count string, the
  version string, and the roadmap status markers are consistent across README,
  ARCHITECTURE, M*.md, and CHANGELOG (the 249→243 class of error becomes
  impossible). Ideally the count is generated, not hand-written.
- **Coverage:** every ADR/RFC referenced exists; every code module has a one-line
  purpose in ARCHITECTURE §repo-layout; every config var in `config.py` appears in
  `.env.example` and the README table (a test asserts the sets match).
- **Diagrams:** re-verify the ARCHITECTURE/README diagrams reflect M5+M6 (add the
  obs/recovery boxes; note they're passive).
- **Deliverable:** `docs/DOC_AUDIT.md` checklist with each item resolved.

---

## 19. Test strategy

**Goal.** Raise confidence with targeted, non-flaky tests — coverage where risk is.

- **New test families:**
  - **Recovery** (`test_recovery.py`) — crash reconcile, idempotency, no data loss.
  - **Replay** (`test_replay.py`) — golden-ledger equivalence, round-trip.
  - **Determinism** (`test_determinism.py`) — repeated-run event equality.
  - **Failure injection** (`test_failure_injection.py`) — provider/DB/tool/emit
    faults → defined outcomes (§12).
  - **Concurrency** (`test_concurrency.py`) — parallel runs, WS fan-out under load,
    the finalize/backfill race made deterministic.
  - **Bench-smoke** (`test_bench_smoke.py`) — the harness runs and stays under
    threshold on a tiny scenario (guards the guard).
  - **Evals** (`test_evals.py`) — the golden goals run and the scorecard generates.
- **Flake elimination:** fix `test_events_after_cursor` root cause (§11) rather than
  retry; institute a "no `sleep`-based synchronization; wait-for-condition" rule.
- **Coverage target:** measure with `coverage.py`; set a floor (e.g. ≥ 85 % on
  `atlas/` core) in CI, ratcheting not absolute.
- **Determinism of the suite:** all agent tests remain model-free (echo/FakeLLM);
  CI never needs Ollama.

---

## 20. Benchmark suite

**Goal.** A committed, reproducible `bench/` that anyone can run and CI enforces.

- **Layout:** `bench/{profile,latency,memory}.py`, `bench/thresholds.json`,
  `bench/out/` (gitignored), `bench/README.md`. One entrypoint `make bench`.
- **Determinism:** driven by `echo`/`ScriptedGateway`; fixed iteration counts;
  warm-up discarded; results reported with variance so noise is visible.
- **CI mode:** `make bench-ci` runs a short subset with generous thresholds (absorb
  runner variance) and fails only on gross regression; full bench is manual/nightly.
- **Trend:** JSON output enables tracking baselines over commits (a simple committed
  history file, no external service).
- **ADR-0020** records the harness design and the deterministic-provider choice.

---

## 21. Evaluation harness

**Goal.** Land the M5-deferred eval harness — measure agent quality reproducibly.

- **Goldens:** 8–12 goals in `evals/goldens/` with expected properties (final-answer
  assertion or rubric, expected max steps, expected tool usage), spanning
  single-task, multi-task, retry, replan, partial, and memory-recall cases.
- **Runner:** `evals/run.py` executes each golden against a **scripted** gateway
  (deterministic, CI-safe) — and optionally a real model locally — scoring
  pass/fail + step count + token/budget usage, writing `evals/scorecard.md`.
- **Scorecard:** committed markdown table (pass-rate, mean steps, budget) + JSON for
  trends; referenced from README (M7 wants it in the README demo).
- **Memory-lift eval:** a paired golden (run A then related run B) asserting B's
  plan is tighter/recalls A — turning the M5 demo into a measured regression guard.
- **Scope note:** the eval harness measures; it does not change agent behavior.

---

## 22. CI improvements

**Goal.** A real pipeline that enforces every guarantee above.

- **Jobs:** backend `pytest` + `ruff check` + `coverage` floor; frontend
  `typecheck` + `build`; `bench-ci` (short); `evals` (scripted); doc-lint (§18).
- **Matrix:** Windows + Linux (determinism/§16 cross-platform); Python 3.14.
- **Caching:** wheel/npm caches; wheel-only installs (ADR-0006) keep CI Ollama-free.
- **Gates:** all green required to merge; bench/coverage regressions fail the build.
- **Artifacts:** upload `bench/out/*` and `scorecard.md` as CI artifacts for trend
  review.
- **Deliverable:** `.github/workflows/ci.yml` (design in RFC; implemented in phase
  5). Cross-cutting backlog item in [TODO](../TODO.md) is resolved here.

---

## 23. Release process

**Goal.** Repeatable, auditable releases.

- **Checklist (`docs/RELEASE.md`):** bump versions (backend `__init__`+`pyproject`,
  frontend `package.json`, `/health`) in lockstep (asserted by test); update
  CHANGELOG `[x.y.z]`; run full bench + evals and paste deltas; tag; verify
  `docker compose up` clean-machine smoke.
- **Versioning policy:** semver within the milestone cadence; additive API changes
  are minor, behavior-preserving hardening is minor (0.6.0), breaking changes gate a
  major and an ADR.
- **Provenance:** CHANGELOG links the RFC/ADRs and the bench/eval deltas for the
  release, so each version's performance and quality posture is on record.

---

## 24. Migration policy

**Goal.** Codify the additive migration contract and apply it to M6's two small
additions.

- **Policy (formalize ADR-0015):** all migrations are additive and idempotent —
  new tables via `create_all`, new columns via guarded `ALTER TABLE … ADD COLUMN`,
  new FTS objects via `CREATE … IF NOT EXISTS` + triggers. **No** drops, renames, or
  type changes. Every migration is downgrade-safe (older code ignores unknown
  tables/columns) — this is invariant **I-27**.
- **M6 additions:** the optional `meta`/`schema_version` row (§14) and any
  benchmark-driven index. Both additive, guarded, tested on a v0.5.0-shaped DB
  (extends the existing migration test).
- **Full migrations:** Alembic remains deferred until Postgres is on the horizon
  (unchanged from M5); the policy documents the trigger for that decision.
- **Recovery interaction:** the reconciler (§13) is migration-aware — it reads only
  columns guaranteed present, so a mixed-version start is safe.

---

## 25. Technical debt audit

**Goal.** Enumerate, triage, and either resolve or consciously accept debt.

- **Sweep:** grep for `TODO`/`FIXME`/`XXX`/`type: ignore`/`noqa`; list each with a
  disposition (fix in M6 / accept-with-reason / defer-to-ticket). Zero silent debt.
- **Known items to address:** hand-maintained `lib/types.ts` (evaluate generating
  from OpenAPI — cross-cutting backlog); the finalize/backfill race (§11); any
  broad `except` that should be narrowed (outside the deliberate best-effort
  boundaries); duplicated view-mapping logic between backend and frontend reducers
  (§15 shared-fixture check mitigates drift).
- **Consistency:** ruff config coverage (E,F,I,UP,B,ANN) — consider adding `S`
  (bandit/security) and `PTH`/`SIM` selectively; validate no new violations.
- **Deliverable:** `docs/TECH_DEBT.md` — the register, with owners and dispositions.

---

## 26. Security review

**Goal.** Harden the existing local-single-user posture; no new auth model.

- **Input validation:** fuzz `?q=` (FTS `MATCH` injection/syntax), path params, and
  request bodies; assert no 500s, no query errors, bounded work. The file tools'
  path jail (`workspace_dir`) re-verified against traversal.
- **SSRF/tooling:** `web_fetch`/`web_search` remain network tools — document that
  they are unauthenticated egress; re-verify URL validation and that they can't read
  local files/localhost metadata endpoints (deny-list/scheme checks).
- **Injection surfaces:** confirm recalled memory is injected as **clearly-delimited
  untrusted hints** (M5, I-19) and that prompt construction can't be escaped by
  crafted goal/lesson text; a test with adversarial lesson content.
- **Secrets/PII:** memory distillation must not persist raw output/PII (I-18) —
  re-audit the writer; ensure logs (§30) never emit secrets or full model I/O at
  default level.
- **Dependencies:** `pip-audit`/`npm audit` in CI (advisory); wheel-only policy
  limits supply-chain surface (ADR-0006).
- **CORS/headers:** re-verify `ATLAS_CORS_ORIGINS` is restrictive by default; no
  wildcard in shipped config.
- **Deliverable:** `docs/SECURITY.md` — threat model (local single-user), reviewed
  surfaces, and accepted risks. Add `ruff` `S` rules where practical.

---

## 27. Configuration review

**Goal.** Every knob validated, documented, and safe by default.

- **Validation:** all `ATLAS_*` settings have types, ranges, and validators
  (budgets > 0, char budgets bounded, enum members checked); invalid config fails
  fast at startup with a clear message, never mid-run.
- **Documentation parity:** a test asserting `Settings` fields ⊆ `.env.example` ⊆
  README table (no undocumented or phantom vars) — feeds the doc audit (§18).
- **Safe defaults:** memory off, reflection off (parity guarantees), CORS
  restrictive, observability off/passive, integrity-check off (fast startup). New
  M6 flags (`ATLAS_METRICS_ENABLED`, `ATLAS_LOG_FORMAT`, `ATLAS_RECOVERY_ENABLED`,
  `ATLAS_DB_INTEGRITY_CHECK`, `busy_timeout`) all default to preserve M5 behavior
  (I-23) — except recovery, whose default is **on** but is behavior-preserving for
  runs that terminated cleanly (only interrupted runs are affected).
- **Precedence:** env > `.env` > defaults, documented; no hardcoded values.

---

## 28. Observability

**Goal.** See inside a running system without changing it (**I-25**).

- **Three passive pillars, all opt-in and best-effort:** metrics (§29), logging
  (§30), and health/readiness. All live in `atlas/obs/` and follow the memory
  best-effort rule — a failure to record is caught, logged once, and swallowed;
  it can never raise into a run.
- **Health vs readiness:** `/health` (liveness + version, existing) and a new
  `/ready` (dependency probe: DB reachable, FTS available, provider configured) so
  orchestrators can distinguish "up" from "able to serve." Readiness is read-only.
- **Correlation:** a `run_id` (and `task_id`) is attached to every log line and
  metric context for that run, so traces are reconstructable — derived from existing
  ids, no new identifiers.
- **No tracing dependency:** span-like timing is done with in-process timers writing
  to the metrics registry; OpenTelemetry is a documented future option, not an M6
  dependency.

---

## 29. Metrics

**Goal.** A tiny, dependency-free, bounded metrics surface.

- **Registry:** an in-process, fixed-cardinality registry (`atlas/obs/metrics.py`) —
  counters and histograms with a **fixed** label set (no per-`run_id` labels →
  bounded memory, I-28). Exposed at `GET /metrics` in a text exposition format
  (Prometheus-compatible) behind `ATLAS_METRICS_ENABLED` (default off).
- **Core metrics:** runs started/completed/failed/cancelled; task attempts;
  reflection verdicts; replans; budget-exhaustions; tool calls by tool + outcome;
  model calls + latency histogram; memory recalls/writes/prunes; event emit count;
  WS subscribers gauge; DB query latency histogram.
- **Passivity:** metric recording is a no-op fast path when disabled and
  exception-isolated when enabled (I-25). A test asserts identical event streams
  with metrics on vs off (I-23).
- **Bounded:** no unbounded label dimensions; histograms use fixed buckets.

---

## 30. Logging

**Goal.** Structured, correlated, secret-safe logs — opt-in JSON for production.

- **Structured option:** `ATLAS_LOG_FORMAT=text|json` (default `text` = current
  behavior). JSON mode emits one object per line with `ts`, `level`, `logger`,
  `run_id`/`task_id` (when in a run context), and message — resolving the
  cross-cutting backlog "structured JSON logging option."
- **Levels/hygiene:** default level unchanged; DEBUG carries prompt/tool detail,
  INFO/WARN never emit full model I/O, secrets, or PII (aligns I-18, §26). A
  redaction pass on known-sensitive fields.
- **Correlation:** a contextvar carries the active `run_id` so any log line inside a
  run is attributable without threading the id through every call.
- **Volume:** logging is best-effort and must not dominate latency (measured in §5);
  no per-event INFO spam on the hot path.

---

## 31. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Benchmark thresholds flaky on shared CI runners → false failures | Med | Med | Generous tolerance band; short CI subset; variance reported; full bench nightly/manual |
| R2 | Reconciler mislabels a run's terminal state (data-derived error) | Low | High | Pure function of ledger; idempotent; extensive crash-sim tests; conservative default (mark FAILED-interrupted only when no completion evidence) |
| R3 | An "optimization" subtly changes event ordering/output | Low | High | I-23/I-24 guarded by replay + determinism tests; each perf change ships with before/after + parity test |
| R4 | Metrics/logging add hot-path overhead | Med | Med | Off by default; no-op fast path; overhead measured in §5 and thresholded |
| R5 | Unbounded metrics cardinality / WS queue growth | Low | High | Fixed label set, bounded queues, leak test (§6), I-28 |
| R6 | SQLite pragma tuning regresses correctness (WAL/sync) | Low | High | Change one pragma at a time behind benchmarks; integrity check (§14); parity tests |
| R7 | Scope creep into resume/PAUSED/code_sandbox | Med | Med | Non-goals (§2) explicit; ADR-0021 fixes "reconcile-not-resume" |
| R8 | Doc-lint too strict → blocks unrelated PRs | Low | Low | Start advisory, then enforce; generate counts rather than match strings |
| R9 | Cross-platform determinism divergence (Windows/Linux float/locale) | Med | Med | Compare structure not raw floats; CI matrix; canonical serialization |

---

## 32. Invariants

New invariants introduced by M6 (continuing the numbering from M5's I-22):

- **I-23 — Hardening is behavior-preserving.** With all M6 configuration at its
  defaults, a run emits the same events in the same order and produces the same
  answer as v0.5.0. (Guarded by replay + determinism + parity tests.)
- **I-24 — Ledger authority / replay determinism.** Derived state (run/task views,
  checklist, answer) is a pure function of the persisted event ledger; replaying a
  ledger reconstructs identical derived state. Instrumentation never alters the
  ledger or its ordering.
- **I-25 — Observability is passive.** Metrics, logging, health/readiness never
  mutate run state and never raise into a run; a recording failure is caught,
  logged once, and swallowed.
- **I-26 — Recovery is ledger-derived.** A reconciled run's terminal state is
  reconstructed solely from persisted events/rows; recovery appends only clearly
  attributed recovery events and is idempotent.
- **I-27 — Additive, downgrade-safe migrations.** No destructive schema change;
  older code tolerates newer additive schema; every migration idempotent.
- **I-28 — Bounded resources.** Every M6 buffer/registry/queue/cache is bounded;
  no unbounded label cardinality or per-run accumulation.

Prior invariants I-1…I-22 are preserved unchanged; M6 adds guards that *enforce*
several of them (notably I-15/I-23 parity and the ledger-sourcing behind I-19).

---

## 33. ADRs

M6 records the following decisions (next numbers after M5's 0016–0019):

- **ADR-0020 — Deterministic benchmark & profiling harness.** Measure framework
  overhead with the echo/scripted providers (LLM latency excluded by design);
  stdlib profilers only; CI enforces a short subset. *Alt rejected:* real-model
  benchmarks (nondeterministic, CI-hostile); external APM (dependency, network).
- **ADR-0021 — Crash recovery: reconcile-to-terminal, not resume.** On startup,
  interrupted runs are reconciled from the ledger to a consistent terminal state;
  execution is not resumed in M6. *Alt rejected:* mid-run resume (large, risky,
  needs re-entrant orchestration — future milestone); leave orphaned RUNNING rows
  (inconsistent, misleading).
- **ADR-0022 — Passive, dependency-free observability.** In-process fixed-cardinality
  metrics + text exposition + structured-log option; opt-in, best-effort. *Alt
  rejected:* Prometheus client / OpenTelemetry (dependencies, cardinality risk in
  v1); no observability (unoperable).
- **ADR-0023 — Replay verification as a first-class test.** A pure reducer folds
  golden ledgers into derived state and asserts equivalence, making ledger-authority
  a checked invariant shared by backend and frontend. *Alt rejected:* trust-by-claim.
- **ADR-0024 — Single-writer concurrency model, documented and hardened.** One
  SQLite writer + WAL readers + per-run asyncio task + single-process EventHub;
  `busy_timeout` and bounded WS queues harden within this ceiling. *Alt rejected:*
  introducing a broker/Postgres now (premature, ADR-0007).

---

## 34. Incremental implementation phases

Each phase is independently reviewable, ships green, and preserves M5 parity.

- **Phase 1 — Measurement foundation.** `bench/` (profile/latency/memory) +
  baselines checked in; `atlas/obs/` skeleton (metrics registry, timers) wired
  passively; `/metrics` + `/ready` behind flags (default off/passive). Deliverables:
  `bench/*`, `PROFILE.md`, `LATENCY`/`MEMORY` baselines, ADR-0020, ADR-0022.
  *Gate:* parity test (metrics on/off identical event stream), bench-smoke green.
- **Phase 2 — Storage & stream optimization.** SQLite pragmas (`busy_timeout`,
  temp_store), index/query review with EXPLAIN plans, FTS optimize/weights, event
  fan-out + serialization tuning, concurrency audit. Each change carries a
  before/after bench delta. Deliverables: `QUERYPLANS.md`, ADR-0024, tuned engine.
  *Gate:* replay + determinism + parity green; measured improvement, no ordering
  change.
- **Phase 3 — Recovery, integrity & replay.** Reconciler (§13), integrity checks
  (§14), replay-verification engine + golden ledgers (§15), determinism harness
  (§16). Deliverables: `atlas/recovery/`, `test_recovery/replay/determinism.py`,
  ADR-0021, ADR-0023, `meta`/schema-version row.
  *Gate:* crash-sim tests, golden-ledger equivalence, idempotent reconcile.
- **Phase 4 — Evaluation harness.** `evals/` goldens + runner + `scorecard.md`,
  including the memory-lift eval (§21). Resolves the M5-deferred item.
  *Gate:* scorecard reproducible under scripted provider in CI.
- **Phase 5 — Audits, CI & release.** API/doc/security/config/tech-debt audits with
  checked-in reports; `.github/workflows/ci.yml` (matrix, coverage floor, bench-ci,
  evals, doc-lint); `docs/{API,SECURITY,RELEASE,TECH_DEBT,DOC_AUDIT}.md`; version →
  **0.6.0**; CHANGELOG, README/ARCHITECTURE/TODO, M6.md, ADR index.
  *Gate:* full suite + CI green; docs consistent; release checklist executed.

Phases 1→2→3 are ordered (measure before optimize; optimize before proving
recovery on the tuned store). Phases 4 and 5 can overlap once Phase 3 lands.

---

## 35. Acceptance criteria

M6 is complete (v0.6.0) when **all** hold:

1. **Parity (I-23).** With defaults, M1–M5 tests are green and a byte-for-byte
   event-stream/answer parity test passes with every M6 subsystem present.
2. **Baselines + guard.** `make bench` produces committed p50/p95 latency, peak RSS,
   and profile tables; `make bench-ci` guards them and fails on gross regression.
3. **Crash recovery.** A crash-simulation test suite proves interrupted runs are
   reconciled to a consistent terminal state with an attributed recovery event, no
   orphaned RUNNING rows, no data loss, and idempotent re-reconcile (I-26).
4. **Replay + determinism.** Golden-ledger equivalence (backend + frontend reducers)
   and repeated-run determinism tests pass on the CI matrix (I-24).
5. **Observability.** `/metrics`, `/ready` work; enabling them changes no run
   behavior and cannot fail a run; metrics are bounded (I-25, I-28); structured JSON
   logging available and secret-safe.
6. **Optimizations proven.** Every storage/query/FTS/stream change has a recorded
   before/after delta and preserves ordering/output.
7. **Evals.** `evals/scorecard.md` generates reproducibly (scripted provider) with
   pass-rate/steps/budget and a memory-lift case.
8. **Audits done.** API, documentation, security, config, and tech-debt audit
   reports are checked in with every item resolved or explicitly accepted; doc-lint
   enforces version/count/config parity.
9. **Process.** CI pipeline (matrix, coverage floor, bench-ci, evals, doc-lint) is
   green and required; release checklist and migration policy documented; versions
   bumped to 0.6.0 in lockstep and asserted.
10. **Quality.** Ruff clean (with any newly adopted rule sets), coverage floor met,
    frontend typecheck + build green, no silent TODO/dead code.

---

*This RFC is design-only. No production code is introduced here; implementation
proceeds by the phases in §34 under separate review, consistent with the M3–M5
cadence.*
