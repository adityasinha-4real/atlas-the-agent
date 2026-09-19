"""In-process fan-out of live events to WebSocket subscribers.

One ``asyncio.Queue`` per subscriber, grouped by ``run_id``. This is deliberately
NOT a message broker (design doc §7): ``publish`` keeps a stable signature so a
real bus (Redis/NATS) can replace it later without touching call sites.
"""

from __future__ import annotations

import asyncio
from collections import defaultdict

from atlas.events.types import Event


class Subscription:
    """A single consumer's live event queue for one run."""

    def __init__(self, run_id: str) -> None:
        self.run_id = run_id
        self._queue: asyncio.Queue[Event] = asyncio.Queue()

    def put_nowait(self, event: Event) -> None:
        self._queue.put_nowait(event)

    async def get(self) -> Event:
        return await self._queue.get()


class EventHub:
    """Registry of subscriptions keyed by run id."""

    def __init__(self) -> None:
        self._subs: dict[str, set[Subscription]] = defaultdict(set)
        self._lock = asyncio.Lock()

    async def subscribe(self, run_id: str) -> Subscription:
        sub = Subscription(run_id)
        async with self._lock:
            self._subs[run_id].add(sub)
        return sub

    async def unsubscribe(self, sub: Subscription) -> None:
        async with self._lock:
            group = self._subs.get(sub.run_id)
            if group is not None:
                group.discard(sub)
                if not group:
                    self._subs.pop(sub.run_id, None)

    async def publish(self, event: Event) -> None:
        """Deliver an event to all current subscribers of its run."""
        async with self._lock:
            targets = list(self._subs.get(event.run_id, ()))
        for sub in targets:
            sub.put_nowait(event)

    def subscriber_count(self) -> int:
        """Total live subscriptions across all runs (read-only; for /metrics)."""
        return sum(len(group) for group in self._subs.values())
