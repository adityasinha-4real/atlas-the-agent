"""The ``emit()`` seam: append an event to the ledger and fan it out live.

A single ``EventEmitter.emit`` (a) allocates the next monotonic ``seq`` for the
run, (b) persists the event (source of truth), and (c) publishes it to live WS
subscribers. Emits are serialized with a lock to keep ``seq`` allocation correct
under concurrency — the single-writer discipline the design doc requires.
"""

from __future__ import annotations

import asyncio
from time import perf_counter

from atlas.events.hub import EventHub
from atlas.events.types import Event, EventType
from atlas.obs.metrics import RuntimeMetrics
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository


class EventEmitter:
    """Process-wide event writer/publisher."""

    def __init__(
        self, db: Database, hub: EventHub, metrics: RuntimeMetrics | None = None
    ) -> None:
        self._db = db
        self._hub = hub
        self._lock = asyncio.Lock()
        # Optional passive metrics (M6). When absent or disabled, emit() is
        # byte-for-byte its M5 self — no timing, no recording (invariant I-23/I-25).
        self._metrics = metrics

    async def emit(
        self, run_id: str, type_: EventType, payload: dict | None = None
    ) -> Event:
        """Append an event and publish it. Returns the persisted event."""
        payload = payload or {}
        metrics = self._metrics
        measuring = metrics is not None and metrics.enabled
        start = perf_counter() if measuring else 0.0
        async with self._lock:
            async with self._db.session() as session:
                repo = EventRepository(session)
                seq = await repo.next_seq(run_id)
                event = await repo.append(run_id, seq, type_, payload)
            # Publish only after the write commits, so subscribers never see an
            # event that isn't durable/backfillable.
            await self._hub.publish(event)
        if measuring:
            metrics.on_event(type_.value, perf_counter() - start)
        return event
