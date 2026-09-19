"""Event sourcing: the ``emit()`` seam, event contracts, and the WS hub.

Per the V2 design, a single ``emit(event)`` appends to the events table (the
source of truth, carrying a monotonic ``seq``) and pushes to the run's live
queue for WebSocket delivery. The events table doubles as trace, replay source,
and WS backfill.
"""
