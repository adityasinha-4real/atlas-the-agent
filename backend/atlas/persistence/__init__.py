"""Persistence: async SQLAlchemy engine, ORM models, and repositories.

SQLite runs in WAL mode with a single-writer discipline (all writes flow through
repositories on the async engine). The event log is the source of truth and the
run ledger; the ``runs`` row is a projection updated as the run progresses.
"""
