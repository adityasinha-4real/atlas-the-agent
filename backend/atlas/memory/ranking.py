"""Recall ranking and budgeted selection (RFC-0003 §8/§9).

Pure, deterministic functions over already-retrieved candidates. Relevance is an
**absolute** term-overlap fraction (not a min-max of the candidate set) so it can
act as a meaningful gate: an unrelated goal scores low and is dropped, rather than
being normalized up to 1.0. Salience and recency are min-max normalized within the
candidate set; ``outcome`` contributes an absolute bias. Ties break deterministically
so recall is reproducible for a given store snapshot.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from atlas.memory.schemas import MemoryOutcome, MemoryView, RecalledMemory

# Fixed weight profile (relevance dominant). Tuned once; not configurable.
_W_RELEVANCE = 0.60
_W_SALIENCE = 0.15
_W_RECENCY = 0.15
_W_OUTCOME = 0.10

# Absolute preference by run outcome: successes rank highest, but a failure still
# carries a useful "what to avoid" lesson, so it is not zero.
_OUTCOME_BIAS: dict[MemoryOutcome, float] = {
    MemoryOutcome.DONE: 1.0,
    MemoryOutcome.PARTIAL: 0.6,
    MemoryOutcome.FAILED: 0.4,
    MemoryOutcome.CANCELLED: 0.0,
}

# A pinned memory is guaranteed a top slot (added to its total).
_PINNED_FLOOR = 1000.0
# Skip a lesson whose token set overlaps an already-picked one this much.
_REDUNDANCY_THRESHOLD = 0.8
_MIN_PER_ITEM_CHARS = 160
_ELLIPSIS = "…"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(value: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(value.lower()) if len(tok) >= 2}


def _relevance(goal_tokens: set[str], memory: MemoryView) -> float:
    """Fraction of goal terms that appear in the memory's text (absolute)."""
    if not goal_tokens:
        return 0.0
    text = f"{memory.goal} {memory.summary} {memory.lessons}"
    hits = goal_tokens & _tokens(text)
    return len(hits) / len(goal_tokens)


def _minmax(values: Sequence[float]) -> list[float]:
    """Min-max normalize to [0, 1]; all-equal (incl. singletons) → all 1.0."""
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-9:
        return [1.0 for _ in values]
    return [(v - lo) / (hi - lo) for v in values]


@dataclass(frozen=True)
class ScoredMemory:
    """A candidate with its absolute relevance and combined score."""

    memory: MemoryView
    relevance: float
    total: float


def score_candidates(
    goal: str, candidates: Sequence[MemoryView], *, now: datetime
) -> list[ScoredMemory]:
    """Score candidates by relevance + salience + recency + outcome bias.

    Returned in descending score order with a deterministic tie-break
    (score, then newest, then id).
    """
    if not candidates:
        return []
    goal_tokens = _tokens(goal)
    relevances = [_relevance(goal_tokens, m) for m in candidates]
    sal_norm = _minmax([m.salience for m in candidates])
    rec_norm = _minmax([m.created_at.timestamp() for m in candidates])

    scored: list[ScoredMemory] = []
    for memory, rel, sal, rec in zip(
        candidates, relevances, sal_norm, rec_norm, strict=True
    ):
        total = (
            _W_RELEVANCE * rel
            + _W_SALIENCE * sal
            + _W_RECENCY * rec
            + _W_OUTCOME * _OUTCOME_BIAS.get(memory.outcome, 0.0)
        )
        if memory.pinned:
            total += _PINNED_FLOOR
        scored.append(ScoredMemory(memory=memory, relevance=rel, total=total))

    scored.sort(key=lambda s: (-s.total, -s.memory.created_at.timestamp(), s.memory.id))
    return scored


def select_within_budget(
    scored: Sequence[ScoredMemory],
    *,
    k: int,
    char_budget: int,
    min_score: float,
) -> list[RecalledMemory]:
    """Greedily pick ≤ k relevant, non-redundant lessons within a char budget.

    Applies the relevance gate (pinned memories are exempt), truncates each lesson
    to a fair share of the budget, and skips near-duplicate lessons (RFC-0003 §8/§9).
    """
    if k <= 0 or char_budget <= 0:
        return []
    per_item = max(char_budget // k, _MIN_PER_ITEM_CHARS)
    selected: list[RecalledMemory] = []
    picked_tokens: list[set[str]] = []
    used = 0

    for cand in scored:
        if len(selected) >= k:
            break
        if not (cand.memory.pinned or cand.relevance >= min_score):
            continue
        lesson = (cand.memory.lessons or cand.memory.summary).strip()
        if not lesson:
            continue
        tokens = _tokens(lesson)
        if any(
            _jaccard(tokens, prior) >= _REDUNDANCY_THRESHOLD for prior in picked_tokens
        ):
            continue
        remaining = char_budget - used
        if remaining <= 0:
            break
        lesson = _truncate(lesson, min(per_item, remaining))
        used += len(lesson)
        picked_tokens.append(tokens)
        selected.append(
            RecalledMemory(memory=cand.memory, score=cand.total, lesson=lesson)
        )
    return selected


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(1, max_chars - 1)].rstrip() + _ELLIPSIS
