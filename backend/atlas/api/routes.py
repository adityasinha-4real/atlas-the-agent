"""REST routes for M1.

Route surface is intentionally minimal (design doc §1.8): exactly what the two
frontend pages need. Memory/tool routes arrive in later milestones.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text

from atlas import __version__
from atlas.agent.schemas import (
    CreateRunRequest,
    HealthView,
    RunSummary,
    RunView,
    TaskView,
)
from atlas.api.deps import AppContext, get_context
from atlas.events.types import Event
from atlas.memory.schemas import MemoryView
from atlas.persistence.repositories import (
    EventRepository,
    RunRepository,
    TaskRepository,
)
from atlas.tools.base import ToolSpec

router = APIRouter()

# Prometheus text exposition content type (RFC-0004 §29).
_METRICS_CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


@router.get("/health", response_model=HealthView, tags=["system"])
async def health(ctx: AppContext = Depends(get_context)) -> HealthView:
    return HealthView(
        app=ctx.settings.app_name,
        version=__version__,
        llm_provider=ctx.settings.llm_provider,
    )


@router.get("/ready", tags=["system"])
async def ready(ctx: AppContext = Depends(get_context)) -> Response:
    """Readiness probe (RFC-0004 §28): can the service actually serve a run?

    Read-only and best-effort — it verifies the database is reachable, whether the
    FTS5 index is present (memory recall degrades to a LIKE scan otherwise), and
    that a provider is configured. Returns 200 when ready, 503 otherwise. Distinct
    from ``/health`` (liveness), which only reports the process is up.
    """
    checks: dict[str, bool] = {}
    try:
        async with ctx.db.session() as session:
            await session.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception:  # noqa: BLE001 - a probe never raises; it reports
        checks["database"] = False
    try:
        async with ctx.db.session() as session:
            await session.execute(text("SELECT 1 FROM memories_fts LIMIT 1"))
        checks["fts5"] = True
    except Exception:  # noqa: BLE001 - FTS5 absent → LIKE fallback, still usable
        checks["fts5"] = False
    checks["provider"] = bool(ctx.settings.llm_provider)

    ready_now = checks["database"] and checks["provider"]
    body = {"ready": ready_now, "version": __version__, "checks": checks}
    code = status.HTTP_200_OK if ready_now else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(body, status_code=code)


@router.get("/metrics", tags=["system"])
async def metrics(ctx: AppContext = Depends(get_context)) -> Response:
    """Prometheus-format metrics (RFC-0004 §29), gated by ``ATLAS_METRICS_ENABLED``.

    Returns 404 when disabled so a scraper sees a clean off-state and enabling the
    endpoint changes no run behavior (invariant I-23). The WS-subscriber gauge is a
    pull metric, sampled from the hub at scrape time.
    """
    if not ctx.settings.metrics_enabled:
        raise HTTPException(status_code=404, detail="metrics disabled")
    ctx.metrics.registry.gauge(
        "atlas_ws_subscribers", "Live WebSocket subscribers"
    ).set(ctx.hub.subscriber_count())
    return PlainTextResponse(
        ctx.metrics.registry.render(), media_type=_METRICS_CONTENT_TYPE
    )


@router.get("/tools", response_model=list[ToolSpec], tags=["system"])
async def list_tools(ctx: AppContext = Depends(get_context)) -> list[ToolSpec]:
    return ctx.registry.specs()


@router.post(
    "/runs",
    response_model=RunView,
    status_code=status.HTTP_201_CREATED,
    tags=["runs"],
)
async def create_run(
    body: CreateRunRequest, ctx: AppContext = Depends(get_context)
) -> RunView:
    return await ctx.run_manager.create_run(body.goal)


@router.get("/runs", response_model=list[RunSummary], tags=["runs"])
async def list_runs(
    limit: int = Query(default=50, ge=1, le=200),
    ctx: AppContext = Depends(get_context),
) -> list[RunSummary]:
    async with ctx.db.session() as session:
        return await RunRepository(session).list_recent(limit=limit)


@router.get("/runs/{run_id}", response_model=RunView, tags=["runs"])
async def get_run(run_id: str, ctx: AppContext = Depends(get_context)) -> RunView:
    async with ctx.db.session() as session:
        row = await RunRepository(session).get(run_id)
        if row is None:
            raise HTTPException(status_code=404, detail="run not found")
        return RunRepository.to_view(row)


@router.get(
    "/runs/{run_id}/events", response_model=list[Event], tags=["runs"]
)
async def get_run_events(
    run_id: str,
    after: int = Query(default=0, ge=0, description="Return events with seq > after"),
    ctx: AppContext = Depends(get_context),
) -> list[Event]:
    async with ctx.db.session() as session:
        run = await RunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return await EventRepository(session).list_after(run_id, after)


@router.get(
    "/runs/{run_id}/tasks", response_model=list[TaskView], tags=["runs"]
)
async def get_run_tasks(
    run_id: str, ctx: AppContext = Depends(get_context)
) -> list[TaskView]:
    """Projection of a run's task list (design doc §1.8, ADR-0009).

    The live checklist is driven by ``plan.created`` / ``task.*`` events; this
    endpoint serves initial load and History replay.
    """
    async with ctx.db.session() as session:
        run = await RunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        return await TaskRepository(session).list_for_run(run_id)


@router.post(
    "/runs/{run_id}/cancel",
    status_code=status.HTTP_202_ACCEPTED,
    tags=["runs"],
)
async def cancel_run(
    run_id: str, ctx: AppContext = Depends(get_context)
) -> dict[str, str]:
    async with ctx.db.session() as session:
        run = await RunRepository(session).get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
    accepted = await ctx.run_manager.cancel_run(run_id)
    return {"run_id": run_id, "cancel_requested": str(accepted).lower()}


# --------------------------------------------------------------------------- #
# Episodic memory (M5, RFC-0003 §16). Additive, read/manage only. Returns an
# empty list when memory is disabled (the store is simply never written to).
# --------------------------------------------------------------------------- #


@router.get("/memories", response_model=list[MemoryView], tags=["memory"])
async def list_memories(
    q: str | None = Query(default=None, description="Full-text search over memories"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    ctx: AppContext = Depends(get_context),
) -> list[MemoryView]:
    """List memories newest-first, or relevance-ranked when ``q`` is given."""
    if q:
        return await ctx.memory_store.search(q, limit=limit)
    return await ctx.memory_store.list_recent(limit=limit, offset=offset)


@router.get("/memories/{memory_id}", response_model=MemoryView, tags=["memory"])
async def get_memory(
    memory_id: str, ctx: AppContext = Depends(get_context)
) -> MemoryView:
    view = await ctx.memory_store.get(memory_id)
    if view is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return view


@router.delete(
    "/memories/{memory_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    tags=["memory"],
)
async def delete_memory(
    memory_id: str, ctx: AppContext = Depends(get_context)
) -> None:
    """Forget a memory (privacy purge, RFC-0003 §14)."""
    if not await ctx.memory_store.delete(memory_id):
        raise HTTPException(status_code=404, detail="memory not found")


@router.post("/memories/{memory_id}/pin", response_model=MemoryView, tags=["memory"])
async def pin_memory(
    memory_id: str, ctx: AppContext = Depends(get_context)
) -> MemoryView:
    """Pin a memory so it is never pruned and is recall-preferred."""
    view = await ctx.memory_store.set_pinned(memory_id, True)
    if view is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return view


@router.post("/memories/{memory_id}/unpin", response_model=MemoryView, tags=["memory"])
async def unpin_memory(
    memory_id: str, ctx: AppContext = Depends(get_context)
) -> MemoryView:
    view = await ctx.memory_store.set_pinned(memory_id, False)
    if view is None:
        raise HTTPException(status_code=404, detail="memory not found")
    return view
