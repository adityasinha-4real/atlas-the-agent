"""WebSocket streaming and replay (event backfill)."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

_TERMINAL = {"run.completed", "run.failed", "run.cancelled"}


def _wait_terminal(client: TestClient, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if client.get(f"/runs/{run_id}").json()["status"] in {
            "done",
            "failed",
            "cancelled",
        }:
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def _drain(ws) -> list[dict]:  # noqa: ANN001
    events: list[dict] = []
    while True:
        event = ws.receive_json()
        events.append(event)
        if event["type"] in _TERMINAL:
            return events


def test_ws_streams_events_until_terminal(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "hello ws"}).json()["id"]
    with client.websocket_connect(f"/runs/{run_id}/stream") as ws:
        events = _drain(ws)

    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert events[-1]["type"] == "run.completed"
    assert any(e["type"] == "answer.token" for e in events)


def test_ws_replays_finished_run_via_backfill(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "replay me"}).json()["id"]
    _wait_terminal(client, run_id)

    # Connecting after completion should replay the full ledger from backfill.
    with client.websocket_connect(f"/runs/{run_id}/stream") as ws:
        events = _drain(ws)

    assert events[0]["seq"] == 1
    assert events[-1]["type"] == "run.completed"


def test_ws_rejects_unknown_run(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    try:
        with client.websocket_connect("/runs/does-not-exist/stream"):
            pass
    except WebSocketDisconnect as exc:
        assert exc.code == 4404
    else:  # pragma: no cover - connection should be rejected
        raise AssertionError("expected the WS to be rejected")
