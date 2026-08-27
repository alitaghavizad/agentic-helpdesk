"""In-process fan-out for the per-user notification SSE channel (spec 10).

Deliberately has no database dependency: durability is the `notifications`
table's job, and this module's only job is telling whoever is currently
connected that something happened. That split is why `publish` to a user
with no subscribers is a no-op rather than an error -- the row is already
safe, and a user who was offline gets it from the replay on connect
instead (spec 8.3 of the phase 6 design).

`publish` is synchronous and never awaits, because it is called from
`after_commit`, which runs inside SQLAlchemy's synchronous commit path.
A subscriber whose queue is full is marked dropped rather than awaited:
a stalled browser must not apply backpressure to an admin's approval
request. The dropped client's stream closes and its next connect replays
whatever it missed from the database.

In-process means single-worker. Running this API under multiple uvicorn
workers would give each worker its own broker, and a user connected to
worker A would not see an event published on worker B. That is an
accepted limit of the spec's `an in-process broker` (spec 10), not an
oversight -- the replay on reconnect keeps the feed *correct* either way,
just not instant.
"""
from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections import defaultdict

_DEFAULT_MAX_QUEUE = 100

_subscribers: dict[uuid.UUID, set["Subscription"]] = defaultdict(set)


class Subscription:
    """One connected SSE client. Iterate it with `await sub.get()`."""

    def __init__(self, user_id: uuid.UUID, max_queue: int) -> None:
        self.user_id = user_id
        self.dropped = False
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)

    def _offer(self, event: dict) -> None:
        if self.dropped:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped = True

    async def get(self) -> dict:
        return await self._queue.get()


@contextlib.contextmanager
def subscribe(user_id: uuid.UUID, *, max_queue: int = _DEFAULT_MAX_QUEUE):
    subscription = Subscription(user_id, max_queue)
    _subscribers[user_id].add(subscription)
    try:
        yield subscription
    finally:
        _subscribers[user_id].discard(subscription)
        if not _subscribers[user_id]:
            del _subscribers[user_id]


def publish(user_id: uuid.UUID, event: dict) -> None:
    for subscription in tuple(_subscribers.get(user_id, ())):
        subscription._offer(event)


def subscriber_count(user_id: uuid.UUID) -> int:
    return len(_subscribers.get(user_id, ()))
