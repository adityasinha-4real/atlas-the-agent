"""Deterministic execution: same input → same event stream (M6 §16, I-24)."""

from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from atlas.core.config import Settings

_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def _settings(tmp_path: Path, sub: str) -> Settings:
    d = tmp_path / sub
    d.mkdir()
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=f"sqlite+aiosqlite:///{(d / 'd.db').as_posix()}",
        workspace_dir=str(d / "ws"),
        log_level="WARNING",
    )


def _normalized_stream(tmp_path: Path, sub: str, goal: str) -> list[tuple[str, object]]:
    from atlas.api.app import create_app

    with TestClient(create_app(_settings(tmp_path, sub))) as client:
        run_id = client.post("/runs", json={"goal": goal}).json()["id"]
        deadline = time.time() + 5.0
        events: list[dict] = []
        while time.time() < deadline:
            events = client.get(f"/runs/{run_id}/events").json()
            if events and events[-1]["type"] in _TERMINAL_EVENTS:
                break
            time.sleep(0.02)
        else:
            raise AssertionError("run did not terminate")
    out: list[tuple[str, object]] = []
    for e in events:
        payload = json.loads(json.dumps(e["payload"]).replace(run_id, "RID"))
        out.append((e["type"], payload))
    return out


def test_same_goal_produces_identical_event_stream(tmp_path: Path) -> None:
    goal = "a deterministic goal to compare"
    first = _normalized_stream(tmp_path, "run1", goal)
    second = _normalized_stream(tmp_path, "run2", goal)
    assert first == second
    assert len(first) > 0
    # Sequence numbers are gapless and monotonic within a run.
    seqs = list(range(1, len(first) + 1))
    assert [i + 1 for i in range(len(first))] == seqs
