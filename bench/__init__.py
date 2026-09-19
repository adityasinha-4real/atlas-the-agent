"""ATLAS benchmark & profiling harness (M6, RFC-0004 §20, ADR-0020).

Deterministic, offline benchmarks driven by the ``echo`` provider and the
persistence layer directly — no Ollama, no network, no wall-clock LLM latency.
The suite isolates *framework* overhead (serialization, DB round-trips, event
fan-out, FTS recall) so optimizations target measured data, not intuition.

Run it with the backend venv:

    backend/.venv/Scripts/python.exe -m bench            # full suite → bench/out/
    backend/.venv/Scripts/python.exe -m bench --ci       # short subset, guard thresholds

This package lives at the repo root; importing it puts ``backend/`` on ``sys.path``
so ``import atlas`` works regardless of the current directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parents[1] / "backend"
if _BACKEND.exists() and str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))
