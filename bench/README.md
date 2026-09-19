# ATLAS benchmarks

Design rationale: [RFC-0004](../docs/rfc/0004-m6-hardening.md) §20.

Deterministic, offline benchmarks. No Ollama, no network — everything runs against
the `echo` provider and the persistence layer, so what's measured is **framework
overhead** (serialization, DB round-trips, event fan-out, FTS recall), not model
latency.

## Run

```bash
# from the repo root, with the backend venv
backend/.venv/Scripts/python.exe -m bench            # full suite -> bench/out/
backend/.venv/Scripts/python.exe -m bench --ci       # short subset + threshold guard
backend/.venv/Scripts/python.exe -m bench --only runs_list
backend/.venv/Scripts/python.exe -m bench --list
backend/.venv/Scripts/python.exe -m bench.profile    # cProfile a full echo run

# or via make
make bench
make bench-ci
```

Output lands in `bench/out/` (git-ignored): `results.json` (machine-readable, for
trend tracking) and `report.md` (human-readable, with captured query plans).

## Scenarios

| Scenario | Measures | RFC |
|---|---|---|
| `emit_event` | one `emit()` (next_seq + insert + publish) | §8 |
| `event_backfill_replay` | loading a full ledger (`list_after`) — the replay read | §8/§15 |
| `memory_recall_fts` | an FTS5 recall query over a seeded store | §10 |
| `runs_list` | the `GET /runs` newest-first list query | §7/§9 |
| `run_end_to_end` | a full echo run (create -> terminal) | §5 |

Each builds its own temp SQLite DB, seeds deterministic fixtures, discards a warm-up
run, then times the hot operation many times. Summaries report p50/p95/p99.

## Thresholds

`thresholds.json` sets generous p95 ceilings; `--ci` fails the build only on a
**gross** regression (a further ±25% tolerance band absorbs shared-runner noise).
They guard against pathological regressions, not micro-noise.

## Baseline

`BASELINE.md` records the committed M6 before/after numbers and the query-plan
improvements from Phase 2. Regenerate the raw report anytime with `make bench`.
