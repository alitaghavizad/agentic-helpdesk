from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import Conversation, Message, MessageRole
from app.rbac.policy import Principal


def create_conversation(
    db: Session, principal: Principal, *, title: str | None = None, guest_name: str | None = None, guest_email: str | None = None,
) -> Conversation:
    # created_at is set explicitly rather than left to the column's
    # server_default. Postgres's now() is the TRANSACTION start time, so any
    # two conversations created inside one transaction would share a
    # byte-identical created_at and the admin conversation list would fall
    # through to ordering by a random uuid4.
    now = datetime.now(timezone.utc)
    if principal.kind == "user":
        conv = Conversation(user_id=uuid.UUID(principal.user_id), title=title, created_at=now)
    else:
        conv = Conversation(
            guest_name=guest_name or "Guest", guest_email=guest_email, title=title,
            created_at=now,
        )
    db.add(conv)
    db.commit()
    db.refresh(conv)
    return conv


def list_conversations(db: Session, principal: Principal) -> list[Conversation]:
    query = db.query(Conversation)
    if principal.kind == "user":
        query = query.filter(Conversation.user_id == uuid.UUID(principal.user_id))
    else:
        return []  # a guest's JWT is bound to one conversation at issue time, not a list they browse
    return query.order_by(Conversation.updated_at.desc()).all()


def get_conversation(db: Session, principal: Principal, conversation_id: uuid.UUID) -> Conversation | None:
    conv = db.get(Conversation, conversation_id)
    if conv is None:
        return None
    if principal.role == "admin":
        return conv
    if principal.kind == "user" and conv.user_id == uuid.UUID(principal.user_id):
        return conv
    if principal.kind == "guest" and conv.guest_email is not None and conv.guest_email == principal.guest_email:
        return conv
    return None


def load_history(db: Session, conversation_id: uuid.UUID) -> list[dict]:
    messages = (
        db.query(Message)
        .filter(Message.conversation_id == conversation_id, Message.role != MessageRole.SYSTEM)
        .order_by(Message.created_at.asc())
        .all()
    )
    return [{"role": m.role.value, "content": m.content} for m in messages]


def append_message(db: Session, conversation_id: uuid.UUID, role: MessageRole, content: list | dict, run_id: uuid.UUID | None = None) -> Message:
    message = Message(conversation_id=conversation_id, role=role, content=content, run_id=run_id)
    db.add(message)
    db.commit()
    db.refresh(message)
    return message


def stage_message(db: Session, conversation_id: uuid.UUID, role: MessageRole, content: list | dict, run_id: uuid.UUID | None = None) -> Message:
    """Same as append_message but WITHOUT committing, so a caller that must
    write the message and something else atomically can do both in one
    transaction. append_message keeps its commit -- the chat turn path relies
    on it."""
    message = Message(conversation_id=conversation_id, role=role, content=content, run_id=run_id)
    db.add(message)
    db.flush()
    return message
