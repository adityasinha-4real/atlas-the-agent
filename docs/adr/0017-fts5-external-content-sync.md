# ADR-0017 — FTS5 external-content sync, with a LIKE fallback

- **Status:** Accepted
- **Date:** 2026-07-10

## Context
[ADR-0004](0004-fts5-over-faiss.md) chose SQLite FTS5 keyword recall over a vector
index for v1. M5 realizes it. FTS5 needs an index kept in sync with the canonical
`memories` rows, and not every SQLite build ships FTS5 — the runtime must not hard-
depend on it. We also keep the single-writer repository discipline (no SQL scattered
through the runtime) and the "light migration" approach ([ADR-0015](0015-light-sqlite-migrations.md))
rather than pulling in Alembic.

## Decision
Index `memories` with an **FTS5 external-content** virtual table
(`memories_fts`, `content='memories'`) covering `goal`/`summary`/`lessons`, kept in
sync by three `AFTER INSERT/UPDATE/DELETE` triggers created in the same idempotent
`create_all` migration step. The canonical text stays in `memories` (no
duplication); the index stores only the inverted terms. At startup an FTS5 probe
(`CREATE VIRTUAL TABLE … IF NOT EXISTS`) runs; if it fails (FTS5 not compiled in),
it is logged and `MemoryRepository.search` **degrades to a `LIKE` scan** over the
same columns. Retrieval is bm25-ordered; final relevance/ranking is computed in
Python (ADR-0018 pipeline), so the store stays swappable behind `MemoryStore`.

## Consequences
- Zero new dependencies; recall is sub-millisecond at v1 scale.
- Triggers keep the index correct inside each write's transaction (single-writer
  discipline preserved).
- The `LIKE` fallback guarantees correctness (slower, no bm25) on FTS5-less builds
  — memory never becomes a hard requirement.
- Trade-off: external-content FTS5 + triggers are a little SQLite-specific DDL to
  carry, but it is idempotent, isolated to the migration, and far lighter than a
  vector store. A semantic tier remains a drop-in behind `MemoryStore`.
