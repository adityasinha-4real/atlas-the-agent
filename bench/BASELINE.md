# M6 benchmark baseline (v0.6.0)

Deterministic echo/persistence path — no model latency. Numbers are from the M6
dev machine (Windows 11, Python 3.14); absolute values vary by host, so treat the
**deltas and query plans** as the signal, not the raw milliseconds. Regenerate the
raw report with `make bench`.

## Phase 2 before / after

| Scenario | Before p50 | After p50 | Before p95 | After p95 | Change |
|---|--:|--:|--:|--:|---|
| `runs_list` | 3.75 ms | **1.46 ms** | 4.67 ms | **1.92 ms** | **~60% faster** (index) |
| `event_backfill_replay` | 14.0 ms | **11.3 ms** | 29.0 ms | **23.2 ms** | ~19% faster (temp_store) |
| `memory_recall_fts` | 3.04 ms | **2.75 ms** | 4.26 ms | **3.75 ms** | ~9% faster (temp_store) |
| `emit_event` | 1.40 ms | 1.88 ms | 1.96 ms | 2.36 ms | within noise (unchanged path) |
| `run_end_to_end_echo` | 121.9 ms | 115.8 ms | 132.2 ms | 126.7 ms | within noise (unchanged path) |

`emit_event` and `run_end_to_end` touch none of the changed code; their run-to-run
spread (±1.5 ms / ±15 ms respectively) exceeds the observed deltas, so they are
reported as unchanged.

## Query-plan improvements (the durable signal)

| Query | Before | After |
|---|---|---|
| `runs_list` (`ORDER BY created_at DESC LIMIT 50`) | `SCAN runs` + `USE TEMP B-TREE FOR ORDER BY` | `SCAN runs USING INDEX ix_runs_created` |
| `event_backfill_replay` (`WHERE run_id=? AND seq>? ORDER BY seq`) | `SEARCH events USING INDEX ix_events_run_seq` | *(unchanged — already optimal)* |

## Changes applied (Phase 2)

1. **`ix_runs_created`** on `runs.created_at` — eliminates the full scan + temp
   B-tree sort behind `GET /runs`. Additive index; declared on the model and
   back-filled on existing DBs by an idempotent `CREATE INDEX IF NOT EXISTS`
   (invariant I-27).
2. **`PRAGMA temp_store=MEMORY`** — keeps FTS/ORDER-BY scratch in memory rather
   than spilling to disk. Affects only transient query scratch space; the database
   file and durability are unchanged (behavior-neutral).

Both are guarded by the parity and storage-correctness tests, and by the CI
threshold guard (`make bench-ci`) going forward.
