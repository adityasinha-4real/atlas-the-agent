"""Replay verification: the ledger is authoritative (M6, RFC-0004 §15, I-24).

Two kinds of check: (1) round-trip — run an echo run, fold its persisted events,
and assert the fold equals the persisted run/task views; (2) golden ledgers —
hand-written event fixtures fold to the expected derived state, covering shapes
the echo provider does not produce (partial abort, cancellation).
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from atlas.agent.schemas import RunStatus, TaskStatus
from atlas.recovery.replay import fold_events, sequence_is_intact

_GOLDEN = Path(__file__).parent / "golden_ledgers"
_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def _run_to_terminal(client: TestClient, goal: str, timeout: float = 5.0) -> str:
    run_id = client.post("/runs", json={"goal": goal}).json()["id"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/runs/{run_id}/events").json()
        if events and events[-1]["type"] in _TERMINAL_EVENTS:
            return run_id
        time.sleep(0.02)
    raise AssertionError("run did not terminate")


def test_roundtrip_fold_matches_persisted_views(client: TestClient) -> None:
    run_id = _run_to_terminal(client, "photosynthesis in plants")
    events = client.get(f"/runs/{run_id}/events").json()
    view = client.get(f"/runs/{run_id}").json()
    tasks = client.get(f"/runs/{run_id}/tasks").json()

    state = fold_events(events)

    # Run status + answer are reproduced purely from the ledger.
    assert state.status.value == view["status"]
    assert state.answer == view["answer"]
    assert state.partial == view["partial"]

    # Every task's derived status + description matches the persisted projection.
    for task in tasks:
        replay_task = state.tasks[task["index"]]
        assert replay_task.status.value == task["status"]
        assert replay_task.description == task["description"]


def _load(name: str) -> list[dict]:
    return json.loads((_GOLDEN / name).read_text(encoding="utf-8"))


def test_golden_simple_done() -> None:
    state = fold_events(_load("simple_done.json"))
    assert state.status is RunStatus.DONE
    assert state.terminal is True
    assert state.answer == "the final answer"
    assert state.task_status_by_index() == {0: TaskStatus.DONE, 1: TaskStatus.DONE}


def test_golden_partial_abort() -> None:
    state = fold_events(_load("partial_abort.json"))
    assert state.status is RunStatus.FAILED
    assert state.partial is True  # FAILED + answer present
    assert state.answer.startswith("PARTIAL")
    assert state.task_status_by_index() == {0: TaskStatus.DONE, 1: TaskStatus.FAILED}
    assert state.error == "Task 2 could not be completed"


def test_golden_cancelled() -> None:
    state = fold_events(_load("cancelled.json"))
    assert state.status is RunStatus.CANCELLED
    assert state.partial is False
    assert state.task_status_by_index() == {0: TaskStatus.DONE, 1: TaskStatus.CANCELLED}


def test_fold_is_deterministic() -> None:
    ledger = _load("simple_done.json")
    a = fold_events(ledger)
    b = fold_events(ledger)
    assert a.status is b.status
    assert a.answer == b.answer
    assert a.task_status_by_index() == b.task_status_by_index()


def test_sequence_integrity_detects_reordering(client: TestClient) -> None:
    """A real ledger is gapless/monotonic; a shuffled copy is detected (I-24)."""
    run_id = _run_to_terminal(client, "ordered ledger check")
    events = client.get(f"/runs/{run_id}/events").json()
    assert sequence_is_intact(events) is True

    # Reorder two events → the sequence is no longer 1..n in order.
    shuffled = list(events)
    shuffled[0], shuffled[-1] = shuffled[-1], shuffled[0]
    assert sequence_is_intact(shuffled) is False

    # A truncated (gapped) stream is also detected.
    assert sequence_is_intact(events[1:]) is False


def test_sequence_integrity_ignores_seqless_fixtures() -> None:
    # Golden ledgers carry no seq; ordering validation is a no-op for them.
    assert sequence_is_intact(_load("simple_done.json")) is True


def test_answer_tokens_reconstruct_answer_without_completed_event() -> None:
    ledger = [
        {"type": "run.created", "payload": {}},
        {"type": "answer.token", "payload": {"text": "hello"}},
        {"type": "answer.token", "payload": {"text": " world"}},
    ]
    state = fold_events(ledger)
    assert state.answer == "hello world"
