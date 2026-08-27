from __future__ import annotations

import asyncio
import uuid

import pytest

from app.notifications import broker


@pytest.mark.asyncio
async def test_publish_reaches_every_subscriber_for_that_user():
    user = uuid.uuid4()
    with broker.subscribe(user) as a, broker.subscribe(user) as b:
        broker.publish(user, {"type": "approval_decided", "id": "1"})
        assert await asyncio.wait_for(a.get(), timeout=1) == {"type": "approval_decided", "id": "1"}
        assert await asyncio.wait_for(b.get(), timeout=1) == {"type": "approval_decided", "id": "1"}


@pytest.mark.asyncio
async def test_publish_does_not_leak_across_users():
    alice, bob = uuid.uuid4(), uuid.uuid4()
    with broker.subscribe(alice) as a, broker.subscribe(bob) as b:
        broker.publish(alice, {"type": "ticket_created"})
        assert await asyncio.wait_for(a.get(), timeout=1) == {"type": "ticket_created"}
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(b.get(), timeout=0.05)


def test_publish_with_no_subscribers_is_a_no_op():
    """The row is already durable in the database; nobody listening is normal,
    not an error. This must never raise."""
    broker.publish(uuid.uuid4(), {"type": "ticket_resolved"})


@pytest.mark.asyncio
async def test_leaving_the_context_unregisters_the_subscriber():
    user = uuid.uuid4()
    with broker.subscribe(user):
        assert broker.subscriber_count(user) == 1
    assert broker.subscriber_count(user) == 0


@pytest.mark.asyncio
async def test_a_subscriber_that_cannot_keep_up_is_dropped_not_allowed_to_block():
    """A stalled SSE client must never apply backpressure to the request that
    is publishing. Once its queue is full the subscriber is marked dropped and
    further events for it are discarded."""
    user = uuid.uuid4()
    with broker.subscribe(user, max_queue=2) as sub:
        for i in range(10):
            broker.publish(user, {"n": i})
        assert sub.dropped is True
