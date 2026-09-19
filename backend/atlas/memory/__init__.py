"""Episodic memory (M5, RFC-0003).

Long-term memory that improves planning by recalling distilled lessons from past
runs. Everything is reached through the ``MemoryStore`` seam so a future semantic
(FAISS) tier is a drop-in ([ADR-0004](../../docs/adr/0004-fts5-over-faiss.md)); the
v1 implementation is episodic and keyword-based (SQLite FTS5).

Memory is **optional** (``ATLAS_MEMORY_ENABLED``, default off) and **best-effort**:
no recall, ranking, distillation, or write failure can ever affect a run
(invariants I-14/I-15). ``service.MemoryService`` is the façade the runtime holds.

This package ``__init__`` deliberately imports nothing: ``persistence.repositories``
imports :mod:`atlas.memory.schemas`, so eager submodule imports here would create a
cycle. Import the concrete classes from their modules
(``atlas.memory.service``, ``atlas.memory.store``, …).
"""

from __future__ import annotations
