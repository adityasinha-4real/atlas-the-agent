"""Dependency accessors bound to application state.

Components are constructed once during the app lifespan and stashed on
``app.state`` (see ``app.py``). Routes depend on the single ``get_context``
accessor and read the singleton they need off ``AppContext``; this keeps the
dependency wiring explicit and testable without a helper per component.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from atlas.core.config import Settings
from atlas.events.hub import EventHub
from atlas.llm.gateway import LLMGateway
from atlas.memory.store import MemoryStore
from atlas.obs.metrics import RuntimeMetrics
from atlas.persistence.database import Database
from atlas.runtime.manager import RunManager
from atlas.tools.registry import ToolRegistry


@dataclass
class AppContext:
    """Container for the app's long-lived singletons."""

    settings: Settings
    db: Database
    hub: EventHub
    gateway: LLMGateway
    registry: ToolRegistry
    run_manager: RunManager
    # Read/manage the episodic memory store (M5). Always available so the
    # ``/memories`` API works; the runtime's recall/write remain gated on
    # ``memory_enabled`` (disabled by default → the store is simply empty).
    memory_store: MemoryStore
    # Passive observability registry (M6). Always present; records only when
    # ``settings.metrics_enabled`` is set.
    metrics: RuntimeMetrics


def get_context(request: Request) -> AppContext:
    return request.app.state.context
