"""REST surface for episodic memory: /memories list/get/delete/pin (M5)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas.core.config import Settings


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


def _mem_settings(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=_sqlite_url(tmp_path / "atlas_mem.db"),
        workspace_dir=str(tmp_path / "workspace"),
        log_level="WARNING",
        memory_enabled=True,
        memory_distill_with_llm=False,  # heuristic write — no scripted LLM needed
        **overrides,  # type: ignore[arg-type]
    )


@pytest.fixture
def mem_client(tmp_path: Path) -> Iterator[TestClient]:
    from atlas.api.app import create_app

    with TestClient(create_app(_mem_settings(tmp_path))) as client:
        yield client


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
    raise AssertionError(f"run {run_id} not terminal in {timeout}s")


def _run_and_wait_memory(client: TestClient, goal: str, timeout: float = 5.0) -> None:
    """Run a goal and wait until a new memory is written (write races run.completed)."""
    before = len(client.get("/memories", params={"limit": 200}).json())
    run_id = client.post("/runs", json={"goal": goal}).json()["id"]
    _wait_terminal(client, run_id)
    deadline = time.time() + timeout
    while time.time() < deadline:
        if len(client.get("/memories", params={"limit": 200}).json()) > before:
            return
        time.sleep(0.02)
    raise AssertionError("memory was not written in time")


def test_memories_empty_initially(mem_client: TestClient) -> None:
    resp = mem_client.get("/memories")
    assert resp.status_code == 200
    assert resp.json() == []


def test_memory_written_and_listed(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "explain a memorable concept clearly")
    body = mem_client.get("/memories").json()
    assert len(body) >= 1
    mem = body[0]
    assert mem["id"].startswith("mem_")
    assert mem["outcome"] == "done"
    assert mem["status"] == "active"
    assert "goal" in mem and "lessons" in mem and "tools_used" in mem


def test_get_memory_by_id_and_404(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "a distinct retrievable goal here")
    listed = mem_client.get("/memories").json()
    mem_id = listed[0]["id"]
    got = mem_client.get(f"/memories/{mem_id}")
    assert got.status_code == 200
    assert got.json()["id"] == mem_id
    assert mem_client.get("/memories/does-not-exist").status_code == 404


def test_delete_memory_and_404(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "a deletable memory goal example")
    mem_id = mem_client.get("/memories").json()[0]["id"]
    assert mem_client.delete(f"/memories/{mem_id}").status_code == 204
    assert mem_client.get(f"/memories/{mem_id}").status_code == 404
    assert mem_client.delete(f"/memories/{mem_id}").status_code == 404


def test_pin_and_unpin(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "a pinnable memory goal example")
    mem_id = mem_client.get("/memories").json()[0]["id"]
    pinned = mem_client.post(f"/memories/{mem_id}/pin")
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True
    unpinned = mem_client.post(f"/memories/{mem_id}/unpin")
    assert unpinned.json()["pinned"] is False
    assert mem_client.post("/memories/missing/pin").status_code == 404


def test_search_filters_by_query(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "convert kilometers to miles quickly")
    _run_and_wait_memory(mem_client, "bake a chocolate sponge cake")
    hits = mem_client.get("/memories", params={"q": "kilometers miles"}).json()
    goals = [m["goal"] for m in hits]
    assert any("kilometers" in g for g in goals)
    assert all("chocolate" not in g for g in goals)


def test_pagination_limit_and_offset(mem_client: TestClient) -> None:
    _run_and_wait_memory(mem_client, "first memorable goal alpha")
    _run_and_wait_memory(mem_client, "second memorable goal bravo")
    page1 = mem_client.get("/memories", params={"limit": 1, "offset": 0}).json()
    page2 = mem_client.get("/memories", params={"limit": 1, "offset": 1}).json()
    assert len(page1) == 1 and len(page2) == 1
    assert page1[0]["id"] != page2[0]["id"]


def test_memory_api_empty_when_disabled(client: TestClient) -> None:
    """With memory disabled (default client), a run writes nothing (I-15)."""
    run_id = client.post("/runs", json={"goal": "a plain goal"}).json()["id"]
    _wait_terminal(client, run_id)
    assert client.get("/memories").json() == []


def test_openapi_documents_memory_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    assert "/memories" in paths
    assert "/memories/{memory_id}" in paths
    assert "/memories/{memory_id}/pin" in paths


def test_health_reports_v060(client: TestClient) -> None:
    assert client.get("/health").json()["version"] == "0.7.0"
