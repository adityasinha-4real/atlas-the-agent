"""The eval harness runs deterministically and every golden passes (M6 §21)."""

from __future__ import annotations

from pathlib import Path

from evals.goldens import GOLDENS
from evals.runner import run_all, run_golden, run_memory_lift


async def test_all_goldens_pass(tmp_path: Path) -> None:
    results = await run_all(tmp_path)
    # One result per golden plus the memory-lift eval.
    assert len(results) == len(GOLDENS) + 1
    failed = [(r.id, r.failures) for r in results if not r.passed]
    assert not failed, f"eval failures: {failed}"


async def test_categories_are_covered(tmp_path: Path) -> None:
    results = await run_all(tmp_path)
    categories = {r.category for r in results}
    # The RFC's spanning set: single/multi/tool/retry/replan/partial/failure/memory.
    assert {
        "single-task",
        "multi-task",
        "tool-use",
        "retry",
        "replan",
        "partial",
        "failure",
        "memory",
    } <= categories


async def test_memory_lift_recalls_prior_run(tmp_path: Path) -> None:
    result = await run_memory_lift(tmp_path)
    assert result.passed, result.failures


async def test_replan_uses_more_steps_than_single_task(tmp_path: Path) -> None:
    # Sanity: the step-count metric is meaningful (replan does more work).
    single = await run_golden(
        next(g for g in GOLDENS if g.id == "single_task"), tmp_path / "s"
    )
    replan = await run_golden(
        next(g for g in GOLDENS if g.id == "replan"), tmp_path / "r"
    )
    assert replan.steps > single.steps


async def test_tool_use_records_a_tool_call(tmp_path: Path) -> None:
    result = await run_golden(
        next(g for g in GOLDENS if g.id == "tool_use"), tmp_path / "t"
    )
    assert result.passed
    assert result.tool_calls >= 1
