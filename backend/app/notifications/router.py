"""The per-user notification feed and its SSE channel (spec 10).

The stream SUBSCRIBES BEFORE IT READS THE BACKLOG. What matters is the
order of the SUBSCRIBE and the DATABASE READ, not the order of the
subscribe and the emit -- reading first and subscribing after would leave
a notification that commits in between in neither place: absent from the
already-taken snapshot, and undelivered because nothing was listening
yet. Subscribing first makes the two sources overlap instead of leaving a
gap, and the `seen` set exists to collapse that overlap.

Events are deduplicated by id so a row that is both replayed and published
arrives once.

A keepalive comment every 15 seconds stops proxies closing an idle stream.
"""
from __future__ import annotations

import asyncio
import json
import uuid

from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.db.session import get_sessionmaker
from app.deps import CurrentPrincipal, DbSession
from app.notifications import broker
from app.notifications import service

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

_KEEPALIVE_SECONDS = 15


class NotificationResponse(BaseModel):
    id: str
    type: str
    title: str
    body: str
    link_type: str | None
    link_id: str | None
    read: bool
    created_at: str | None


def _serialize(row) -> NotificationResponse:
    return NotificationResponse(
        id=str(row.id), type=row.type.value, title=row.title, body=row.body,
        link_type=row.link_type, link_id=str(row.link_id) if row.link_id else None,
        read=row.read_at is not None,
        created_at=row.created_at.isoformat() if row.created_at else None,
    )


def _require_user(principal) -> uuid.UUID:
    """Guests have no notifications: notifications.user_id is NOT NULL and a
    guest is not a row in `users` (spec 5.1)."""
    if principal.kind != "user" or not principal.user_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "guests have no notification feed")
    return uuid.UUID(principal.user_id)


@router.get("", response_model=list[NotificationResponse])
def list_notifications(
    principal: CurrentPrincipal, db: DbSession, unread_only: bool = False,
) -> list[NotificationResponse]:
    user_id = _require_user(principal)
    return [_serialize(r) for r in service.list_for_user(db, user_id, unread_only=unread_only)]


@router.post("/{notification_id}/read", response_model=NotificationResponse)
def mark_notification_read(
    notification_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession,
) -> NotificationResponse:
    user_id = _require_user(principal)
    row = service.mark_read(db, user_id, notification_id)
    if row is None:
        # Same 404 whether the row is missing or belongs to someone else, so
        # the endpoint does not leak which ids exist.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such notification")
    db.commit()
    return _serialize(row)


@router.get("/stream")
async def stream_notifications(principal: CurrentPrincipal) -> StreamingResponse:
    """Takes NO `db: DbSession` dependency, on purpose.

    FastAPI tears the dependency stack down only after the response is
    complete, and an SSE response completes when the client disconnects. A
    dependency-provided session would therefore stay checked out of the
    pool -- and, once it had read anything, `idle in transaction` -- for the
    entire life of the stream. Measured: three open streams held three such
    backends. The pool is pool_size=5 + max_overflow=10, so about fifteen
    concurrent streams exhaust it and every other request (login, chat,
    approvals) blocks and then fails; long-lived open snapshots also hold
    back xmin and stop VACUUM from reclaiming anything. The backlog read
    below gets its own session, closed before the keepalive loop begins,
    so an idle stream holds no connection at all.
    """
    user_id = _require_user(principal)

    async def event_stream():
        # SUBSCRIBE FIRST, THEN READ THE BACKLOG. The database read must
        # happen after the subscription exists, or a notification committed
        # between the two is in neither: absent from the snapshot because it
        # committed after the query, and undelivered because nothing was
        # listening yet. Subscribing first makes the two sources overlap
        # instead of leaving a gap, and `seen` below collapses that overlap.
        with broker.subscribe(user_id) as subscription:
            # Read the whole backlog into memory and drop the session before
            # the first yield, so no connection is held while the generator
            # is parked. Serializing here rather than while yielding also
            # keeps every ORM attribute access inside the session's lifetime.
            with get_sessionmaker()() as db:
                backlog = [
                    {
                        "type": r.type.value, "id": str(r.id), "title": r.title, "body": r.body,
                        "link_type": r.link_type, "link_id": str(r.link_id) if r.link_id else None,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in service.list_for_user(db, user_id, unread_only=True)
                ]
            # `seen` holds the backlog ids and nothing else, and never grows
            # after this point. That is sufficient, not a shortcut: the only
            # duplicate possible is a row that is both replayed from the
            # database and published live during the overlap window above.
            # The broker publishes each notification to a subscriber exactly
            # once, so a live event carrying an id that was NOT in the
            # backlog can never arrive twice -- adding live ids would grow
            # the set without bound, for the life of the stream, to guard
            # against a repeat that cannot happen.
            seen = {event["id"] for event in backlog}
            for event in backlog:
                yield f"data: {json.dumps(event)}\n\n"
            while True:
                try:
                    event = await asyncio.wait_for(subscription.get(), timeout=_KEEPALIVE_SECONDS)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                except broker.SubscriberDropped:
                    # This client fell too far behind and the broker stopped
                    # queueing for it. Close the stream rather than pretend it is
                    # still live: the browser reconnects and the replay above
                    # re-delivers everything it missed from the database, so
                    # nothing durable is lost.
                    return
                if event.get("id") in seen:
                    continue
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
