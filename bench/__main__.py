"""Benchmark CLI (RFC-0004 §20).

    python -m bench                 # full suite → bench/out/{results.json,report.md}
    python -m bench --ci            # short subset; exit non-zero on threshold breach
    python -m bench --only NAME     # run a single scenario
    python -m bench --list          # list scenarios

Deterministic and offline (echo provider). Thresholds live in
``bench/thresholds.json`` and are compared with a tolerance band so shared CI
runners do not flap (default 25%).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from pathlib import Path

from bench import scenarios
from bench._harness import Result, read_rss_bytes, summarize, write_reports

_ROOT = Path(__file__).resolve().parents[1]
_OUT = _ROOT / "bench" / "out"
_THRESHOLDS = _ROOT / "bench" / "thresholds.json"


async def _run(names: list[str]) -> list[Result]:
    results: list[Result] = []
    with tempfile.TemporaryDirectory(prefix="atlas-bench-") as tmp:
        workdir = Path(tmp)
        for name in names:
            scenario = scenarios.ALL[name]
            # Warm-up run discarded (JIT/connection/page-cache priming).
            await scenario(workdir / f"{name}-warmup")
            results.append(await scenario(workdir / name))
    return results


def _check_thresholds(summaries: list[dict[str, object]], tolerance: float) -> int:
    if not _THRESHOLDS.exists():
        print("no thresholds.json; skipping guard")
        return 0
    thresholds = json.loads(_THRESHOLDS.read_text(encoding="utf-8"))
    breaches = 0
    for summary in summaries:
        limit = thresholds.get(summary["name"], {}).get("p95_seconds")
        if limit is None:
            continue
        allowed = limit * (1.0 + tolerance)
        actual = float(summary["p95"])
        status = "ok" if actual <= allowed else "BREACH"
        if status == "BREACH":
            breaches += 1
        print(
            f"  {summary['name']:<24} p95={actual * 1e3:8.3f} ms "
            f"limit={limit * 1e3:8.3f} ms (+{tolerance:.0%}) -> {status}"
        )
    return breaches


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench", description="ATLAS benchmarks")
    parser.add_argument("--ci", action="store_true", help="run the short CI subset")
    parser.add_argument("--only", help="run a single scenario by name")
    parser.add_argument("--list", action="store_true", help="list scenarios and exit")
    parser.add_argument(
        "--tolerance", type=float, default=0.25, help="CI threshold tolerance band"
    )
    args = parser.parse_args(argv)

    if args.list:
        for name in scenarios.ALL:
            tag = " (ci)" if name in scenarios.CI_SUBSET else ""
            print(f"{name}{tag}")
        return 0

    if args.only:
        names = [args.only]
    elif args.ci:
        names = list(scenarios.CI_SUBSET)
    else:
        names = list(scenarios.ALL)

    print(f"running {len(names)} scenario(s): {', '.join(names)}")
    results = asyncio.run(_run(names))
    summaries = [summarize(r) for r in results]
    rss = read_rss_bytes()
    json_path, md_path = write_reports(summaries, _OUT, rss=rss)
    print(f"wrote {json_path.relative_to(_ROOT)} and {md_path.relative_to(_ROOT)}")

    for summary in summaries:
        print(
            f"  {summary['name']:<24} "
            f"p50={float(summary['p50']) * 1e3:8.3f} ms  "
            f"p95={float(summary['p95']) * 1e3:8.3f} ms  n={summary['n']}"
        )

    if args.ci:
        print("threshold guard:")
        breaches = _check_thresholds(summaries, args.tolerance)
        if breaches:
            print(f"FAIL: {breaches} threshold breach(es)")
            return 1
        print("all within thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
