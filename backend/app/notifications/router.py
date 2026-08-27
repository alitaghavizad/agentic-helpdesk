"""The per-user notification feed and its SSE channel (spec 10).

The stream SUBSCRIBES BEFORE IT REPLAYS. Replaying first and subscribing
afterwards would silently drop anything published in the gap between the
two, which is exactly the window a busy admin approving a queue would hit.
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


def _serialize(row) -> NotificationResponse:
    return NotificationResponse(
        id=str(row.id), type=row.type.value, title=row.title, body=row.body,
        link_type=row.link_type, link_id=str(row.link_id) if row.link_id else None,
        read=row.read_at is not None,
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
async def stream_notifications(principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    user_id = _require_user(principal)
    unread = [
        {
            "type": r.type.value, "id": str(r.id), "title": r.title, "body": r.body,
            "link_type": r.link_type, "link_id": str(r.link_id) if r.link_id else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in service.list_for_user(db, user_id, unread_only=True)
    ]

    async def event_stream():
        seen: set[str] = set()
        with broker.subscribe(user_id) as subscription:
            for event in unread:
                seen.add(event["id"])
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
                seen.add(event.get("id"))
                yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")
