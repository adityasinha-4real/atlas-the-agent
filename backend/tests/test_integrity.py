"""Persistence integrity + schema stamp (M6, RFC-0004 §14)."""

from __future__ import annotations

import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from atlas import __version__
from atlas.core.config import Settings
from atlas.persistence.database import SCHEMA_VERSION, Database
from atlas.recovery.integrity import check_integrity, read_schema_meta


def _url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


async def test_quick_check_passes_on_fresh_db(tmp_path: Path) -> None:
    db = Database(_url(tmp_path / "ok.db"))
    await db.create_all()
    try:
        report = await check_integrity(db)
        assert report.quick_check_ok is True
        assert report.fts_in_sync is True
        assert report.healthy is True
    finally:
        await db.dispose()


async def test_schema_meta_is_stamped(tmp_path: Path) -> None:
    db = Database(_url(tmp_path / "meta.db"))
    await db.create_all()
    try:
        meta = await read_schema_meta(db)
        assert meta["schema_version"] == str(SCHEMA_VERSION)
        assert meta["app_version"] == __version__
    finally:
        await db.dispose()


async def test_schema_meta_stamp_is_idempotent(tmp_path: Path) -> None:
    db = Database(_url(tmp_path / "meta2.db"))
    await db.create_all()
    await db.create_all()  # second create_all upserts, does not duplicate
    try:
        meta = await read_schema_meta(db)
        assert meta["schema_version"] == str(SCHEMA_VERSION)
    finally:
        await db.dispose()


@pytest.fixture
def integrity_client(tmp_path: Path) -> Iterator[TestClient]:
    from atlas.api.app import create_app

    settings = Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=_url(tmp_path / "app.db"),
        workspace_dir=str(tmp_path / "ws"),
        log_level="WARNING",
        db_integrity_check=True,
    )
    with TestClient(create_app(settings)) as client:
        yield client


def test_app_boots_with_integrity_check_enabled(integrity_client: TestClient) -> None:
    # With the fail-fast check on, a healthy DB still serves normally.
    assert integrity_client.get("/health").status_code == 200
    run_id = integrity_client.post("/runs", json={"goal": "still works"}).json()["id"]
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if integrity_client.get(f"/runs/{run_id}").json()["status"] in {
            "done",
            "failed",
            "cancelled",
        }:
            break
        time.sleep(0.02)
    assert integrity_client.get(f"/runs/{run_id}").json()["status"] == "done"
