"""Fan-out of what is happening, to every connected client.

Broadcast rather than per-client, so the phone showing the status and a laptop
browser watching over its shoulder stay in step for free.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from typing import Any

#: Per-subscriber buffer. Pose-driven state updates arrive ~30/s, so a slow or
#: backgrounded client must never be allowed to stall the session loop - it
#: loses the frames it could not keep up with instead.
_QUEUE_SIZE = 64


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        #: last value of each event type, replayed to a client on connect so a
        #: freshly opened page shows the current state rather than an empty one
        self._latest: dict[str, dict[str, Any]] = {}

    def publish(self, type_: str, **data: Any) -> None:
        """Never blocks and never raises. Called from the hot path."""
        event = {"type": type_, **data}
        self._latest[type_] = event
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # drop for this subscriber only; a stalled client is its own
                # problem and must not become everyone else's
                pass

    @contextlib.asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[dict[str, Any]]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_QUEUE_SIZE)
        for event in self._latest.values():
            with contextlib.suppress(asyncio.QueueFull):
                queue.put_nowait(event)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
