"""Uvicorn entrypoint. Run with ``python -m atlas.main`` or ``uvicorn``.

Exposes a module-level ``app`` so process managers can target ``atlas.main:app``.
"""

from __future__ import annotations

import uvicorn

from atlas.api.app import create_app
from atlas.core.config import get_settings

app = create_app()


def main() -> None:
    settings = get_settings()
    uvicorn.run(
        "atlas.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        reload=settings.environment == "development",
    )


if __name__ == "__main__":
    main()
