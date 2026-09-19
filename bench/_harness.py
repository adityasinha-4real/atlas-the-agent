"""Shared benchmark plumbing: timing, percentiles, RSS, and report writers.

Kept dependency-free (stdlib only). ``Result`` holds raw per-operation samples;
``summarize`` turns them into p50/p95/p99 + mean/min/max. Reports are written as
both machine-readable JSON (trend tracking) and human-readable markdown.
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path


def environment() -> dict[str, object]:
    """Capture the benchmark environment for reproducibility (RFC-0004 review rec)."""
    return {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "processor": platform.processor() or platform.machine(),
        "cpu_count": os.cpu_count(),
    }


@dataclass
class Result:
    """Raw samples for one benchmark scenario."""

    name: str
    unit: str
    samples: list[float]
    meta: dict[str, object] = field(default_factory=dict)


def _percentile(ordered: Sequence[float], pct: float) -> float:
    if not ordered:
        return 0.0
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    frac = rank - low
    return ordered[low] + (ordered[high] - ordered[low]) * frac


def summarize(result: Result) -> dict[str, object]:
    """Compute summary statistics for a scenario's samples."""
    ordered = sorted(result.samples)
    n = len(ordered)
    return {
        "name": result.name,
        "unit": result.unit,
        "n": n,
        "p50": _percentile(ordered, 50),
        "p95": _percentile(ordered, 95),
        "p99": _percentile(ordered, 99),
        "mean": statistics.fmean(ordered) if ordered else 0.0,
        "min": ordered[0] if ordered else 0.0,
        "max": ordered[-1] if ordered else 0.0,
        "meta": result.meta,
    }


def read_rss_bytes() -> int | None:
    """Best-effort resident-set-size of the current process (bytes) or None.

    Uses the platform API (Windows ``psapi`` via ctypes, POSIX ``resource``); any
    failure returns None so the harness degrades gracefully (RFC-0004 §6).
    """
    try:  # POSIX
        import resource

        maxrss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KiB, macOS reports bytes; normalize to bytes heuristically.
        return maxrss * 1024 if maxrss < 10**9 else maxrss
    except Exception:  # noqa: BLE001 - not POSIX; try Windows
        pass
    try:  # Windows
        import ctypes
        from ctypes import wintypes

        class _PROCESS_MEMORY_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD),
                ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = _PROCESS_MEMORY_COUNTERS()
        counters.cb = ctypes.sizeof(_PROCESS_MEMORY_COUNTERS)
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        if ctypes.windll.psapi.GetProcessMemoryInfo(
            handle, ctypes.byref(counters), counters.cb
        ):
            return int(counters.WorkingSetSize)
    except Exception:  # noqa: BLE001 - RSS is optional
        return None
    return None


def _fmt_seconds(value: float) -> str:
    if value >= 1.0:
        return f"{value:.3f} s"
    if value >= 1e-3:
        return f"{value * 1e3:.3f} ms"
    return f"{value * 1e6:.1f} µs"


def to_markdown(summaries: list[dict[str, object]], *, rss: int | None) -> str:
    env = environment()
    lines = [
        "# ATLAS benchmark results",
        "",
        f"_Generated {datetime.now(UTC).isoformat(timespec='seconds')} · "
        "deterministic echo/persistence path (no model latency)._",
        "",
        f"_Env: Python {env['python']} · {env['platform']} · "
        f"{env['cpu_count']} CPU._",
        "",
        "| Scenario | n | p50 | p95 | p99 | mean |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for s in summaries:
        lines.append(
            f"| `{s['name']}` | {s['n']} | {_fmt_seconds(float(s['p50']))} | "
            f"{_fmt_seconds(float(s['p95']))} | {_fmt_seconds(float(s['p99']))} | "
            f"{_fmt_seconds(float(s['mean']))} |"
        )
    if rss is not None:
        lines += ["", f"Peak RSS: **{rss / (1024 * 1024):.1f} MiB**"]
    # Surface any query plans captured in scenario metadata.
    plans = [(s["name"], s["meta"].get("query_plan")) for s in summaries
             if isinstance(s.get("meta"), dict) and s["meta"].get("query_plan")]
    if plans:
        lines += ["", "## Query plans", ""]
        for name, plan in plans:
            lines += [f"- `{name}`: {plan}"]
    return "\n".join(lines) + "\n"


def write_reports(
    summaries: list[dict[str, object]], out_dir: Path, *, rss: int | None
) -> tuple[Path, Path]:
    """Write ``results.json`` + ``report.md`` into ``out_dir``. Returns their paths."""
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": environment(),
        "rss_bytes": rss,
        "results": summaries,
    }
    json_path = out_dir / "results.json"
    md_path = out_dir / "report.md"
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    md_path.write_text(to_markdown(summaries, rss=rss), encoding="utf-8")
    return json_path, md_path
