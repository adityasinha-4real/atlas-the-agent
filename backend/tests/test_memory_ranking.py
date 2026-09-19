"""Recall ranking and budgeted selection (RFC-0003 §8/§9)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from atlas.memory.ranking import score_candidates, select_within_budget
from atlas.memory.schemas import MemoryOutcome, MemoryView

_NOW = datetime(2026, 7, 10, tzinfo=UTC)


def _view(
    mid: str,
    *,
    goal: str,
    lessons: str = "some lesson",
    outcome: MemoryOutcome = MemoryOutcome.DONE,
    salience: float = 1.0,
    pinned: bool = False,
    age_days: int = 0,
) -> MemoryView:
    created = _NOW - timedelta(days=age_days)
    return MemoryView(
        id=mid,
        run_id=None,
        goal=goal,
        outcome=outcome,
        success=outcome is MemoryOutcome.DONE,
        summary="",
        lessons=lessons,
        salience=salience,
        pinned=pinned,
        created_at=created,
        updated_at=created,
    )


def test_relevance_orders_by_term_overlap() -> None:
    candidates = [
        _view("m1", goal="bake a chocolate cake"),
        _view("m2", goal="tallest mountain height in meters"),
    ]
    scored = score_candidates("height of the tallest mountain", candidates, now=_NOW)
    assert scored[0].memory.id == "m2"
    assert scored[0].relevance > scored[1].relevance


def test_relevance_is_absolute_not_minmax() -> None:
    """An unrelated candidate keeps a low absolute relevance (gate stays meaningful)."""
    candidates = [_view("m1", goal="photosynthesis in plants")]
    scored = score_candidates("convert kilometers to miles", candidates, now=_NOW)
    assert scored[0].relevance == 0.0


def test_pinned_sorts_first_regardless_of_relevance() -> None:
    candidates = [
        _view("m1", goal="highly relevant mountain height query"),
        _view("m2", goal="totally unrelated", pinned=True),
    ]
    scored = score_candidates("mountain height", candidates, now=_NOW)
    assert scored[0].memory.id == "m2"


def test_recency_breaks_relevance_ties() -> None:
    candidates = [
        _view("old", goal="convert units quickly", age_days=100),
        _view("new", goal="convert units quickly", age_days=0),
    ]
    scored = score_candidates("convert units", candidates, now=_NOW)
    assert scored[0].memory.id == "new"


def test_select_respects_k() -> None:
    scored = score_candidates(
        "convert units",
        [
            _view(f"m{i}", goal="convert units of length", lessons=word)
            for i, word in enumerate(["alpha", "bravo", "charlie", "delta", "echo"])
        ],
        now=_NOW,
    )
    picked = select_within_budget(scored, k=2, char_budget=800, min_score=0.0)
    assert len(picked) == 2


def test_select_applies_min_score_gate() -> None:
    scored = score_candidates(
        "convert kilometers to miles",
        [_view("m1", goal="unrelated cooking recipe")],
        now=_NOW,
    )
    picked = select_within_budget(scored, k=3, char_budget=800, min_score=0.15)
    assert picked == []


def test_select_pinned_bypasses_min_score() -> None:
    scored = score_candidates(
        "convert kilometers to miles",
        [_view("m1", goal="unrelated cooking recipe", pinned=True)],
        now=_NOW,
    )
    picked = select_within_budget(scored, k=3, char_budget=800, min_score=0.99)
    assert [p.memory.id for p in picked] == ["m1"]


def test_select_truncates_to_char_budget() -> None:
    long_lesson = "word " * 400  # ~2000 chars
    scored = score_candidates(
        "convert units",
        [_view("m1", goal="convert units", lessons=long_lesson)],
        now=_NOW,
    )
    picked = select_within_budget(scored, k=3, char_budget=200, min_score=0.0)
    assert len(picked) == 1
    assert len(picked[0].lesson) <= 200


def test_select_skips_redundant_lessons() -> None:
    shared = "always use the calculator tool for arithmetic conversions here"
    scored = score_candidates(
        "convert units",
        [
            _view("m1", goal="convert units a", lessons=shared),
            _view("m2", goal="convert units b", lessons=shared),
        ],
        now=_NOW,
    )
    picked = select_within_budget(scored, k=3, char_budget=800, min_score=0.0)
    assert len(picked) == 1  # the near-duplicate second lesson is dropped
