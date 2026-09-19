# ADR-0006 — Wheel-only installs and plain uvicorn on Python 3.14

- **Status:** Accepted
- **Date:** 2026-07-09

## Context
The only interpreter available in the development environment is CPython 3.14.
Several ecosystem packages did not yet publish 3.14 wheels at the originally
pinned versions: `pydantic-core` fell back to a Rust source build that failed to
link on Windows, and `uvicorn[standard]` pulls Rust-based `watchfiles`/`httptools`
that risk the same.

## Decision
1. Choose dependency **floors** (not exact old pins) so pip resolves versions
   that ship prebuilt `cp314` wheels (e.g. `pydantic>=2.11` → 2.13.x with a
   `cp314` wheel).
2. Install with `--only-binary=:all:` so any missing wheel fails fast instead of
   silently triggering a source build.
3. Use **plain `uvicorn` + pure-Python `websockets`** instead of the
   `[standard]` extra, avoiding Rust build dependencies. Uvicorn's stat-based
   reloader covers dev needs.
4. The **Docker image uses `python:3.12-slim`** for the broadest wheel coverage;
   application code targets 3.11+.

## Consequences
- Reproducible, source-build-free installs on the dev machine and in CI.
- Slightly newer library versions than the review assumed — behavior unchanged
  for our usage.
- If a future dependency lacks a 3.14 wheel, prefer a 3.12 venv locally rather
  than compiling from source on Windows.
