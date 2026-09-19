"""REST surface: health, run creation, retrieval, listing, events, cancel."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

_TERMINAL_EVENTS = {"run.completed", "run.failed", "run.cancelled"}


def _wait_until_terminal(client: TestClient, run_id: str, timeout: float = 5.0) -> dict:
    """Wait until the run's terminal event is persisted, then return its view.

    Waiting on the terminal *event* (not just the run status) closes the
    finalize/backfill race: the status flips to ``done`` a hair before the final
    ``run.completed`` event is committed, so a status-only wait can read the ledger
    one event early (RFC-0004 §11/§19).
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        events = client.get(f"/runs/{run_id}/events").json()
        if events and events[-1]["type"] in _TERMINAL_EVENTS:
            return client.get(f"/runs/{run_id}").json()
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not reach a terminal state in {timeout}s")


def test_health(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["app"] == "ATLAS"
    assert body["llm_provider"] == "echo"


def test_list_tools(client: TestClient) -> None:
    resp = client.get("/tools")
    assert resp.status_code == 200
    names = {t["name"] for t in resp.json()}
    assert {"calculator", "web_search", "web_fetch", "file_read", "file_write"} <= names


def test_create_run_returns_201_and_view(client: TestClient) -> None:
    resp = client.post("/runs", json={"goal": "say hello"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["goal"] == "say hello"
    assert body["status"] in {"created", "running", "done"}
    assert body["id"]


def test_empty_goal_rejected(client: TestClient) -> None:
    assert client.post("/runs", json={"goal": "   "}).status_code == 422


def test_run_completes_with_echoed_answer(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "quantum"}).json()["id"]
    body = _wait_until_terminal(client, run_id)
    assert body["status"] == "done"
    assert "quantum" in body["answer"]


def test_events_are_ordered_and_complete(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "alpha beta"}).json()["id"]
    _wait_until_terminal(client, run_id)

    events = client.get(f"/runs/{run_id}/events").json()
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs)
    assert seqs[0] == 1

    types = [e["type"] for e in events]
    assert "run.created" in types
    assert "answer.token" in types
    assert "run.completed" in types


def test_events_after_cursor(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "one two"}).json()["id"]
    _wait_until_terminal(client, run_id)
    all_events = client.get(f"/runs/{run_id}/events").json()
    after = all_events[0]["seq"]
    tail = client.get(f"/runs/{run_id}/events", params={"after": after}).json()
    assert all(e["seq"] > after for e in tail)
    assert len(tail) == len(all_events) - 1


def test_list_runs(client: TestClient) -> None:
    client.post("/runs", json={"goal": "first"})
    client.post("/runs", json={"goal": "second"})
    runs = client.get("/runs").json()
    goals = {r["goal"] for r in runs}
    assert {"first", "second"} <= goals


def test_run_tasks_projection(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "photosynthesis"}).json()["id"]
    _wait_until_terminal(client, run_id)

    resp = client.get(f"/runs/{run_id}/tasks")
    assert resp.status_code == 200
    tasks = resp.json()
    # echo plans a single task equal to the goal; the run completes it.
    assert len(tasks) == 1
    assert tasks[0]["index"] == 0
    assert tasks[0]["status"] == "done"
    assert "photosynthesis" in tasks[0]["description"]


def test_plan_and_task_events_present(client: TestClient) -> None:
    run_id = client.post("/runs", json={"goal": "mitochondria"}).json()["id"]
    _wait_until_terminal(client, run_id)
    types = [e["type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert "plan.created" in types
    assert "task.started" in types
    assert "task.completed" in types


def test_task_view_exposes_m4_fields(client: TestClient) -> None:
    # Additive M4 projection fields are serialized on the tasks endpoint.
    run_id = client.post("/runs", json={"goal": "ribosomes"}).json()["id"]
    _wait_until_terminal(client, run_id)
    task = client.get(f"/runs/{run_id}/tasks").json()[0]
    assert task["attempt_count"] == 1  # one attempt on a clean echo run
    assert task["replan_generation"] == 0
    assert task["parent_generation"] is None


def test_run_view_exposes_partial_flag(client: TestClient) -> None:
    # RunView carries the additive `partial` flag; a clean run is not partial.
    run_id = client.post("/runs", json={"goal": "osmosis"}).json()["id"]
    body = _wait_until_terminal(client, run_id)
    assert body["partial"] is False
    assert body["status"] == "done"


def test_unknown_run_404(client: TestClient) -> None:
    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/events").status_code == 404
    assert client.get("/runs/nope/tasks").status_code == 404
    assert client.post("/runs/nope/cancel").status_code == 404
