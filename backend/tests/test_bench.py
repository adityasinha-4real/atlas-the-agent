"""The benchmark harness itself: summaries, thresholds, and scenario smoke (M6).

The suite lives at the repo root (``bench/``), so these tests put the root on the
path and exercise a small, fast slice of each scenario — enough to prove the
framework runs deterministically and reports sane numbers, without the full
seed sizes used for real measurement.
"""

from __future__ import annotations

import json
from pathlib import Path

from bench import scenarios
from bench._harness import Result, read_rss_bytes, summarize

_ROOT = Path(__file__).resolve().parents[2]


def test_summarize_percentiles() -> None:
    result = Result("demo", "s/op", [float(i) for i in range(100)])
    summary = summarize(result)
    assert summary["n"] == 100
    assert abs(float(summary["p50"]) - 49.5) < 0.01
    assert abs(float(summary["p95"]) - 94.05) < 0.01
    assert float(summary["min"]) == 0.0
    assert float(summary["max"]) == 99.0


def test_summarize_empty_is_safe() -> None:
    summary = summarize(Result("empty", "s/op", []))
    assert summary["n"] == 0
    assert float(summary["p95"]) == 0.0


def test_thresholds_cover_ci_subset() -> None:
    thresholds = json.loads(
        (_ROOT / "bench" / "thresholds.json").read_text(encoding="utf-8")
    )
    # Every CI scenario's Result name must have a p95 ceiling to guard against.
    result_names = {"emit_event", "runs_list", "run_end_to_end_echo"}
    for name in result_names:
        assert name in thresholds
        assert thresholds[name]["p95_seconds"] > 0


def test_rss_reader_returns_int_or_none() -> None:
    rss = read_rss_bytes()
    assert rss is None or (isinstance(rss, int) and rss > 0)


async def test_emit_scenario_runs(tmp_path: Path) -> None:
    result = await scenarios.emit_event(tmp_path, n=15)
    assert result.name == "emit_event"
    assert len(result.samples) == 15
    assert all(s >= 0 for s in result.samples)


async def test_replay_read_scenario_runs(tmp_path: Path) -> None:
    # Covers "replay timing": loading a full ledger via list_after.
    result = await scenarios.event_backfill_replay(tmp_path, events=40, reps=5)
    assert result.meta["events"] == 40
    assert "query_plan" in result.meta
    assert len(result.samples) == 5


async def test_runs_list_scenario_runs(tmp_path: Path) -> None:
    result = await scenarios.runs_list(tmp_path, runs=60, reps=5)
    assert "ix_runs_created" in str(result.meta["query_plan"])
    assert len(result.samples) == 5


async def test_recall_scenario_runs(tmp_path: Path) -> None:
    result = await scenarios.memory_recall_fts(tmp_path, memories=30, reps=5)
    assert result.meta["memories"] == 30
    assert len(result.samples) == 5
