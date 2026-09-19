# ADR-0004 — SQLite FTS5 episodic memory over FAISS in v1

- **Status:** Accepted (reverses V1)
- **Date:** 2026-07-09

## Context
V1 put three-tier memory (working/episodic/semantic) with FAISS, an embedding
model, and an ingestion pipeline into the core. At v1 scale (dozens–hundreds of
run summaries) vector recall is indistinguishable from keyword recall, and the
demo value of semantic memory is invisible in a short review.

## Decision
V1 ships **episodic memory only**: one `memories` table holding per-run
structured summaries (`goal, outcome, lessons`), recalled at plan time via
**SQLite FTS5 keyword search** — built into SQLite, no new dependency. All access
goes through a `MemoryStore` interface so FAISS can drop in later unchanged.

## Consequences
- Removes an embedding model and index from the critical path.
- "Agent recalls lessons from past runs" demos identically to a vector store.
- FAISS/semantic memory becomes a bounded, interface-compatible upgrade (M6+).

_FTS5 availability confirmed in the target runtime. Realized in code at M5._
