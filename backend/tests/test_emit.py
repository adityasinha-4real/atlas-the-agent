"""The emit() seam: persistence + live fan-out with correct sequencing."""

from __future__ import annotations

import asyncio

from atlas.events.emit import EventEmitter
from atlas.events.hub import EventHub
from atlas.events.types import EventType
from atlas.persistence.database import Database
from atlas.persistence.repositories import EventRepository, RunRepository


async def _seed_run(database: Database, run_id: str) -> None:
    async with database.session() as session:
        await RunRepository(session).create(run_id, "goal")


async def test_emit_assigns_monotonic_seq_and_persists(database: Database) -> None:
    await _seed_run(database, "r")
    emitter = EventEmitter(database, EventHub())

    e1 = await emitter.emit("r", EventType.RUN_CREATED, {})
    e2 = await emitter.emit("r", EventType.RUN_STARTED, {})
    assert (e1.seq, e2.seq) == (1, 2)

    async with database.session() as session:
        stored = await EventRepository(session).list_after("r", 0)
    assert [e.type for e in stored] == [EventType.RUN_CREATED, EventType.RUN_STARTED]


async def test_emit_publishes_to_live_subscriber(database: Database) -> None:
    await _seed_run(database, "r")
    hub = EventHub()
    emitter = EventEmitter(database, hub)

    sub = await hub.subscribe("r")
    await emitter.emit("r", EventType.ANSWER_TOKEN, {"text": "hi"})

    event = await asyncio.wait_for(sub.get(), timeout=1.0)
    assert event.type is EventType.ANSWER_TOKEN
    assert event.payload == {"text": "hi"}


async def test_concurrent_emits_keep_seq_unique(database: Database) -> None:
    await _seed_run(database, "r")
    emitter = EventEmitter(database, EventHub())

    events = await asyncio.gather(
        *(emitter.emit("r", EventType.LOG, {"i": i}) for i in range(20))
    )
    seqs = sorted(e.seq for e in events)
    assert seqs == list(range(1, 21))
