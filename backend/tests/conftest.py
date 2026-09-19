"""Shared pytest fixtures.

Two testing styles:
* Component tests build ``Database``/``EventEmitter``/``RunManager`` directly on
  the test's own event loop (fast, precise).
* API/WS tests use Starlette's ``TestClient`` (which runs the app lifespan and
  supports WebSockets) against a temp SQLite DB and the deterministic ``echo``
  provider — no Ollama required.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from atlas.core.config import Settings
from atlas.persistence.database import Database

# Put the repo root on the path so tests can ``import bench`` (the benchmark
# harness lives at the repo root, a sibling of ``backend/``) (M6, RFC-0004 §20).
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _sqlite_url(path: Path) -> str:
    return f"sqlite+aiosqlite:///{path.as_posix()}"


@pytest.fixture
def test_settings(tmp_path: Path) -> Settings:
    return Settings(
        environment="test",
        llm_provider="echo",
        llm_model="echo",
        database_url=_sqlite_url(tmp_path / "atlas_test.db"),
        workspace_dir=str(tmp_path / "workspace"),
        cors_origins=["http://localhost:3000"],
        log_level="WARNING",
    )


@pytest_asyncio.fixture
async def database(tmp_path: Path) -> AsyncIterator[Database]:
    db = Database(_sqlite_url(tmp_path / "atlas_unit.db"))
    await db.create_all()
    try:
        yield db
    finally:
        await db.dispose()


@pytest.fixture
def client(test_settings: Settings) -> Iterator[TestClient]:
    # Import here so app construction picks up the injected settings.
    from atlas.api.app import create_app

    app = create_app(test_settings)
    with TestClient(app) as test_client:
        yield test_client
