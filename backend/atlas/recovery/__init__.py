"""Crash recovery, replay verification, and persistence integrity (M6, RFC-0004).

Everything here derives state **solely from the persisted ledger** (invariant
I-26): the reconciler brings runs interrupted by a crash to a consistent terminal
state without ever resuming execution or fabricating history (ADR-0021), and the
replay reducer proves derived state is a pure fold of events (I-24, ADR-0023).
"""
