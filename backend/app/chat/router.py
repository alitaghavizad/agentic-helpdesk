from __future__ import annotations

import json
import uuid

import anthropic
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.loop import run_turn
from app.chat.service import append_message, create_conversation, get_conversation, list_conversations, load_history
from app.config import get_settings
from app.db.models import MessageRole
from app.deps import CurrentPrincipal, DbSession

router = APIRouter(prefix="/api/conversations", tags=["chat"])

# Module-level singleton, constructed lazily so importing this module
# doesn't require ANTHROPIC_API_KEY to be set (e.g. under the default,
# stub-driven test suite) -- overridden wholesale in tests via
# monkeypatch.setattr(router_module, "_anthropic_client", FakeAnthropicClient(...)).
_anthropic_client: object | None = None


def _get_client():
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = anthropic.AsyncAnthropic(api_key=get_settings().anthropic_api_key)
    return _anthropic_client


class CreateConversationRequest(BaseModel):
    title: str | None = None


class ConversationResponse(BaseModel):
    id: str
    title: str | None
    status: str


class SendMessageRequest(BaseModel):
    content: str


def _serialize(conv) -> ConversationResponse:
    return ConversationResponse(id=str(conv.id), title=conv.title, status=conv.status.value)


@router.post("", response_model=ConversationResponse)
def create_conversation_endpoint(payload: CreateConversationRequest, principal: CurrentPrincipal, db: DbSession) -> ConversationResponse:
    conv = create_conversation(db, principal, title=payload.title, guest_name=principal.guest_name, guest_email=principal.guest_email)
    return _serialize(conv)


@router.get("", response_model=list[ConversationResponse])
def list_conversations_endpoint(principal: CurrentPrincipal, db: DbSession) -> list[ConversationResponse]:
    return [_serialize(c) for c in list_conversations(db, principal)]


@router.get("/{conversation_id}", response_model=ConversationResponse)
def get_conversation_endpoint(conversation_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> ConversationResponse:
    conv = get_conversation(db, principal, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")
    return _serialize(conv)


@router.post("/{conversation_id}/messages")
async def send_message_endpoint(conversation_id: uuid.UUID, payload: SendMessageRequest, principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    conv = get_conversation(db, principal, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")

    history = load_history(db, conversation_id)
    append_message(db, conversation_id, MessageRole.USER, [{"type": "text", "text": payload.content}])
    user_key = principal.user_id if principal.kind == "user" else (conv.guest_email or str(conversation_id))

    async def event_stream():
        assistant_content: list = []
        run_id: str | None = None
        async for event in run_turn(
            _get_client(), db, principal, conversation_id=conversation_id,
            user_key=user_key, history=history, user_message=payload.content,
        ):
            if event.type == "token":
                assistant_content.append({"type": "text", "text": event.data["text"]})
            if event.type == "done":
                run_id = event.data.get("run_id")
            yield f"data: {json.dumps({'type': event.type, **event.data})}\n\n"
        if assistant_content:
            append_message(db, conversation_id, MessageRole.ASSISTANT, assistant_content, run_id=uuid.UUID(run_id) if run_id else None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
