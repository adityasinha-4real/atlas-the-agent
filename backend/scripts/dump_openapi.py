"""Dump the backend's OpenAPI schema to ``frontend/openapi.json``.

The frontend's typed API client is generated from this file (M7), so it is the
committed contract between backend and frontend. Regenerate after any change to a
route or a response model:

    cd backend && .venv/Scripts/python.exe -m scripts.dump_openapi
    cd frontend && npm run gen:api

This imports the app factory only — it never starts the server, opens the
database, or contacts a model, so it is fully offline and deterministic.
"""

from __future__ import annotations

import json
from pathlib import Path

from atlas.api.app import create_app
from atlas.core.config import Settings

_OUT = Path(__file__).resolve().parents[2] / "frontend" / "openapi.json"


def main() -> None:
    # Echo provider: no Ollama needed and no effect on the schema (the surface is
    # provider-independent). Building the app does not run the lifespan.
    app = create_app(Settings(llm_provider="echo"))
    schema = app.openapi()
    _OUT.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    paths = len(schema.get("paths", {}))
    print(f"wrote {_OUT} ({paths} paths)")


if __name__ == "__main__":
    main()
