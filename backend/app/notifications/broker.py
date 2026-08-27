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


class SubscriberDropped(RuntimeError):
    """Raised to a consumer whose subscription fell behind and was dropped.
    The SSE endpoint turns this into a closed stream; the client reconnects
    and replays what it missed from the `notifications` table, which is why
    dropping a slow subscriber loses nothing durable."""


class Subscription:
    """One connected SSE client. Await sub.get() to receive the next event,
    or catch SubscriberDropped if the subscription fell behind."""

    def __init__(self, user_id: uuid.UUID, max_queue: int) -> None:
        self.user_id = user_id
        self.dropped = False
        self._queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=max_queue)
        try:
            self._loop = asyncio.get_running_loop()
        except RuntimeError:
            # Subscribed from sync context (only tests do this). Nothing is
            # awaiting the queue, so a direct put_nowait is safe.
            self._loop = None

    def _put(self, event: dict) -> None:
        if self.dropped:
            return
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            self.dropped = True

    def _offer(self, event: dict) -> None:
        """publish() is called from SQLAlchemy's after_commit, which runs on
        whichever thread committed -- and because this project's endpoints
        are sync `def`, Starlette runs them in a threadpool, NOT on the
        event loop. Touching an asyncio.Queue from another thread races the
        loop's internals, so cross-thread offers are marshalled back onto
        the owning loop."""
        if self.dropped:
            return
        if self._loop is not None:
            try:
                running = asyncio.get_running_loop()
            except RuntimeError:
                running = None
            if running is not self._loop:
                self._loop.call_soon_threadsafe(self._put, event)
                return
        self._put(event)

    async def get(self) -> dict:
        """Raises SubscriberDropped once a dropped subscriber's buffered
        events are exhausted.

        Needs no wakeup mechanism, and that is worth understanding rather
        than trusting: a subscriber is dropped ONLY when its queue is full,
        so a consumer blocked here on an empty queue cannot be dropped
        underneath it. The two states are mutually exclusive."""
        if self.dropped and self._queue.empty():
            raise SubscriberDropped(
                f"subscriber for {self.user_id} fell behind and was dropped"
            )
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
