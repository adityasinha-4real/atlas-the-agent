"""WebSocket streaming of run events.

Ordering/reconnection strategy (design doc §1.7): the client may pass ``?after=N``
to resume. The server subscribes to live events FIRST, then backfills persisted
events with ``seq > after`` from the ledger, then streams live events filtering by
``seq`` — so no event is missed or duplicated across the subscribe/backfill seam.
This same mechanism powers replay of finished runs (backfill only, then close).
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from atlas.api.deps import AppContext
from atlas.events.types import Event, EventType
from atlas.persistence.repositories import EventRepository, RunRepository

logger = logging.getLogger(__name__)

router = APIRouter()

_TERMINAL = {
    EventType.RUN_COMPLETED,
    EventType.RUN_FAILED,
    EventType.RUN_CANCELLED,
}


def _dump(event: Event) -> dict:
    return event.model_dump(mode="json")


@router.websocket("/runs/{run_id}/stream")
async def stream_run(
    websocket: WebSocket,
    run_id: str,
    after: int = Query(default=0, ge=0),
) -> None:
    ctx: AppContext = websocket.app.state.context

    # Validate the run exists before accepting, to fail fast with a clear code.
    async with ctx.db.session() as session:
        run = await RunRepository(session).get(run_id)
    if run is None:
        await websocket.close(code=4404)
        return

    await websocket.accept()
    subscription = await ctx.hub.subscribe(run_id)
    last_seq = after
    try:
        # 1) Backfill persisted events after the cursor.
        async with ctx.db.session() as session:
            backlog = await EventRepository(session).list_after(run_id, after)
        for event in backlog:
            await websocket.send_json(_dump(event))
            last_seq = event.seq
            if event.type in _TERMINAL:
                return  # replay/catch-up of a finished run

        # 2) Stream live events, skipping any already delivered via backfill.
        while True:
            event = await subscription.get()
            if event.seq <= last_seq:
                continue
            await websocket.send_json(_dump(event))
            last_seq = event.seq
            if event.type in _TERMINAL:
                return
    except WebSocketDisconnect:
        logger.debug("WS client disconnected from run %s", run_id)
    except asyncio.CancelledError:  # server shutdown
        raise
    finally:
        await ctx.hub.unsubscribe(subscription)
        try:
            await websocket.close()
        except RuntimeError:
            pass
