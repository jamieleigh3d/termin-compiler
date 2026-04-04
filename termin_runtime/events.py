"""EventBus pub/sub system for the Termin runtime."""

import asyncio
from datetime import datetime


class EventBus:
    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []
        self._event_log: list[dict] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        if q in self._subscribers:
            self._subscribers.remove(q)

    async def publish(self, event: dict):
        event.setdefault("log_level", "INFO")
        event.setdefault("timestamp", datetime.now().isoformat())
        self._event_log.append(event)
        # Keep only last 1000 events
        if len(self._event_log) > 1000:
            self._event_log = self._event_log[-1000:]
        for q in self._subscribers:
            await q.put(event)

    def get_event_log(self):
        return list(self._event_log)
