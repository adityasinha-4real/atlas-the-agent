"""ATLAS evaluation harness (M6, RFC-0004 §21).

Reproducible, offline agent-quality evals. Each golden drives a real ``RunManager``
through a deterministic ``ScriptedGateway`` (the FakeLLM) — no Ollama, no network —
and scores pass/fail plus step count and model/tool usage. The harness *measures*
the agent; it never changes agent behavior.

Goldens span the paths the echo provider cannot naturally produce: single- and
multi-task plans, tool use, retry, replan, graceful partial abort, and a paired
memory-lift eval (run A then a related run B recalls A's lesson).

This package lives at the repo root; importing it puts ``backend/`` on
``sys.path`` so ``import atlas`` works regardless of the current directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND.exists() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
