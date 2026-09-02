"""The message shape shared by the chat and admin routers.

It lives here rather than in either router because both publish it: the
owner's transcript (GET /api/conversations/{id}) and the admin's
(GET /api/admin/conversations/{id}) are the same rows seen by different
callers, and two independently-maintained copies of that shape would
drift the moment one of them gained a field.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.db.models import Message, MessageRole


class MessageView(BaseModel):
    """`content` is the stored content-block list exactly as the model
    exchanged it -- text blocks, image blocks, tool results. It is `Any`
    because that union is defined by the Anthropic API, not by us, and
    narrowing it here would silently drop block kinds we do not yet know."""
    id: str
    role: str
    content: Any
    created_at: str | None
    run_id: str | None


def to_message_view(message: Message) -> MessageView:
    return MessageView(
        id=str(message.id),
        role=message.role.value,
        content=message.content,
        created_at=message.created_at.isoformat() if message.created_at else None,
        run_id=str(message.run_id) if message.run_id else None,
    )


def transcript_of(db, conversation_id) -> list[MessageView]:
    """System messages are excluded, matching chat.service.load_history:
    they carry the system prompt, which is ours and not the requester's."""
    rows = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role != MessageRole.SYSTEM)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [to_message_view(row) for row in rows]
