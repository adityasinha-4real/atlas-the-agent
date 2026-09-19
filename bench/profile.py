"""cProfile a full echo run to attribute framework overhead (RFC-0004 §4).

    backend/.venv/Scripts/python.exe -m bench.profile [--runs N] [--top K]

Deterministic (echo provider); LLM latency is excluded by construction, so the
top of the profile is ATLAS's own overhead — serialization, DB round-trips, event
fan-out. Writes a text profile to ``bench/out/profile.txt`` and prints the top K.
"""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import pstats
import tempfile
from pathlib import Path

from bench import scenarios

_OUT = Path(__file__).resolve().parents[1] / "bench" / "out"


async def _drive(runs: int) -> None:
    with tempfile.TemporaryDirectory(prefix="atlas-prof-") as tmp:
        await scenarios.run_end_to_end(Path(tmp), reps=runs)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.profile")
    parser.add_argument("--runs", type=int, default=20)
    parser.add_argument("--top", type=int, default=25)
    args = parser.parse_args(argv)

    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(_drive(args.runs))
    profiler.disable()

    buffer = io.StringIO()
    stats = pstats.Stats(profiler, stream=buffer).sort_stats("cumulative")
    stats.print_stats(args.top)
    text = buffer.getvalue()

    _OUT.mkdir(parents=True, exist_ok=True)
    (_OUT / "profile.txt").write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {(_OUT / 'profile.txt')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
