"""Eval CLI (RFC-0004 §21).

    backend/.venv/Scripts/python.exe -m evals            # run all → evals/scorecard.md
    backend/.venv/Scripts/python.exe -m evals --json     # also print results.json path

Deterministic and offline (scripted gateway). Writes a committed ``scorecard.md``
(pass-rate, per-eval steps/tool usage) and ``results.json`` for trend tracking.
Exits non-zero if any eval fails, so CI can gate on it.
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from evals.runner import EvalResult, run_all

_ROOT = Path(__file__).resolve().parents[1]
_SCORECARD = _ROOT / "evals" / "scorecard.md"
_RESULTS = _ROOT / "evals" / "results.json"


def _scorecard_md(results: list[EvalResult]) -> str:
    passed = sum(1 for r in results if r.passed)
    total = len(results)
    rate = (passed / total * 100.0) if total else 0.0
    lines = [
        "# ATLAS eval scorecard",
        "",
        f"_Generated {datetime.now(UTC).isoformat(timespec='seconds')} · "
        "deterministic scripted gateway (no model)._",
        "",
        f"**Pass rate: {passed}/{total} ({rate:.0f}%)**",
        "",
        "| Eval | Category | Status | Result | Steps | Tools | Notes |",
        "|---|---|---|:--:|--:|--:|---|",
    ]
    for r in results:
        mark = "✅" if r.passed else "❌"
        notes = "; ".join(r.failures) if r.failures else ""
        lines.append(
            f"| `{r.id}` | {r.category} | {r.status} | {mark} | "
            f"{r.steps} | {r.tool_calls} | {notes} |"
        )
    lines += [
        "",
        "_Steps = model (LLM) calls; a proxy for reasoning work and token budget._",
    ]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    show_json = "--json" in (argv or sys.argv[1:])
    with tempfile.TemporaryDirectory(prefix="atlas-evals-") as tmp:
        results = asyncio.run(run_all(Path(tmp)))

    _SCORECARD.write_text(_scorecard_md(results), encoding="utf-8")
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "results": [asdict(r) for r in results],
    }
    _RESULTS.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    passed = sum(1 for r in results if r.passed)
    print(f"evals: {passed}/{len(results)} passed -> {_SCORECARD.relative_to(_ROOT)}")
    for r in results:
        mark = "ok " if r.passed else "FAIL"
        detail = "" if r.passed else "  " + "; ".join(r.failures)
        print(f"  [{mark}] {r.id:<22} steps={r.steps} tools={r.tool_calls}{detail}")
    if show_json:
        print(f"json -> {_RESULTS.relative_to(_ROOT)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
