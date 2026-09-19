"""ATLAS — a hand-built agentic AI runtime (plan → act → reflect → recover).

This package is organized around stable seams (see ``ATLAS-Design-Review-V2.md``):
``llm.LLMGateway``, ``events.emit``, the repository pattern, and the run-as-
persisted-ledger. As of M4 a goal is planned into an ordered task list, each task
is executed by the ReAct loop and judged by a reflector that can retry, replan, or
gracefully abort within per-run budgets — all streamed as persisted, replayable
events. M5 adds opt-in **episodic memory**: finished runs are distilled into
lessons (SQLite FTS5) and recalled at plan time to improve related runs. M6
hardens the runtime — benchmarks, passive observability, crash recovery, replay
verification, and an eval harness — with every addition off/passive by default
so behavior stays byte-for-byte M5 (invariant I-23).
"""

__version__ = "0.7.0"
