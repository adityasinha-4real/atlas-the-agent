"""FastAPI application factory and lifespan wiring.

All long-lived singletons (DB, event hub, LLM gateway, run manager) are built
once during the lifespan and attached to ``app.state.context``. ``create_app``
accepts an optional ``Settings`` so tests can inject the ``echo`` provider and a
temporary database.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from atlas import __version__
from atlas.api import routes, ws
from atlas.api.deps import AppContext
from atlas.core.config import Settings, get_settings
from atlas.core.logging import configure_logging
from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.llm.gateway import build_gateway
from atlas.memory.store import EpisodicStore
from atlas.obs.metrics import MetricsRegistry, RuntimeMetrics
from atlas.persistence.database import Database
from atlas.recovery.integrity import check_integrity
from atlas.recovery.reconciler import Reconciler
from atlas.runtime.manager import RunManager
from atlas.tools import build_registry

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.database_url)
        await db.create_all()
        # Optional fail-fast integrity check (M6 §14; default off → no startup cost).
        if settings.db_integrity_check:
            await check_integrity(db)
        hub = EventHub()
        # Passive metrics (M6): recording is a no-op when disabled, so the emitter
        # and every downstream path stay byte-for-byte M5 by default (I-23/I-25).
        metrics = RuntimeMetrics(
            MetricsRegistry(), enabled=settings.metrics_enabled, version=__version__
        )
        emitter = EventEmitter(db, hub, metrics=metrics)
        # Crash recovery (M6 §13): reconcile any runs left non-terminal by a prior
        # crash to a consistent terminal state, derived from the ledger (I-26). A
        # no-op on a clean DB, so cleanly-terminated runs are unaffected (I-23).
        if settings.recovery_enabled:
            await Reconciler(db, emitter).reconcile_pending()
        gateway = build_gateway(settings)
        registry = build_registry(settings)
        run_manager = RunManager(db, emitter, gateway, registry, settings)
        memory_store = EpisodicStore(db)

        app.state.context = AppContext(
            settings=settings,
            db=db,
            hub=hub,
            gateway=gateway,
            registry=registry,
            run_manager=run_manager,
            memory_store=memory_store,
            metrics=metrics,
        )
        logger.info(
            "ATLAS %s started (provider=%s, model=%s)",
            __version__,
            settings.llm_provider,
            settings.llm_model,
        )
        try:
            yield
        finally:
            await run_manager.shutdown()
            await gateway.aclose()
            await db.dispose()
            logger.info("ATLAS shut down cleanly")

    app = FastAPI(
        title="ATLAS API",
        version=__version__,
        summary="Agentic AI runtime — plan, act, reflect, recover.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(routes.router)
    app.include_router(ws.router)
    return app
