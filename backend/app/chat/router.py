from __future__ import annotations

import json
import logging
import uuid

import anthropic
from fastapi import APIRouter, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.loop import run_turn
from app.chat.schemas import MessageView, transcript_of
from app.chat.service import append_message, create_conversation, derive_conversation_title, get_conversation, list_conversations, load_history, stage_message
from app.config import get_settings
from app.db.models import MessageRole
from app.deps import CurrentPrincipal, DbSession

logger = logging.getLogger(__name__)

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
    messages: list[MessageView] = []


class SendMessageRequest(BaseModel):
    content: str


def _serialize(conv, messages: list[MessageView] | None = None) -> ConversationResponse:
    """`messages` defaults to empty rather than being loaded here on purpose:
    the list endpoint shares this serializer, and a sidebar of conversations
    must not read the whole message table to render its titles. Only the
    by-id endpoint passes a transcript."""
    return ConversationResponse(
        id=str(conv.id), title=conv.title, status=conv.status.value,
        messages=messages or [],
    )


def build_attachment_blocks(db, conversation_id: uuid.UUID):
    """Returns (blocks, attachments) for the attachments waiting on this
    conversation. Wrapping happens here rather than in the multimodal package
    because this is the point where content crosses into the model's view --
    the same place RAG results are wrapped (spec 12.1).

    Each block is a separate content block rather than being concatenated into
    the user's text, so the boundary between what the user typed and what a
    file said stays explicit in the transcript."""
    from app.agent.guardrails import scan_for_injection, wrap_untrusted
    from app.multimodal import service as attachments

    pending = attachments.pending_for_conversation(db, conversation_id)
    blocks = []
    for attachment in pending:
        flags = scan_for_injection(attachment.parsed_text or "")
        if flags:
            # Recorded, not removed. Spec 12.1 is explicit that flagged
            # content still reaches the model with its flag, so the model can
            # report the attempt instead of being silently shielded from it.
            logger.warning(
                "injection markers in attachment %s: %s", attachment.id, ", ".join(flags),
            )
        blocks.append({
            "type": "text",
            "text": wrap_untrusted(
                source=f"attachment/{attachment.filename}",
                content=attachment.parsed_text or "",
            ),
        })
    return blocks, pending


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
    return _serialize(conv, transcript_of(db, conversation_id))


@router.post("/{conversation_id}/messages")
async def send_message_endpoint(conversation_id: uuid.UUID, payload: SendMessageRequest, principal: CurrentPrincipal, db: DbSession) -> StreamingResponse:
    conv = get_conversation(db, principal, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")

    if conv.title is None:
        # Best-effort, from the first user message only -- see
        # derive_conversation_title's own docstring for why this exists at
        # all. Later messages never override it: a conversation's title is
        # set once, the same way its first message sets its subject.
        derived_title = derive_conversation_title(payload.content)
        if derived_title:
            conv.title = derived_title

    history = load_history(db, conversation_id)
    attachment_blocks, pending_attachments = build_attachment_blocks(db, conversation_id)
    user_content = attachment_blocks + [{"type": "text", "text": payload.content}]
    # stage_message (not append_message) so the write below is uncommitted --
    # binding must land in the SAME transaction as the message. Committing the
    # message first would leave a window where its content has already been
    # delivered but the attachments are still marked pending, so the next
    # turn would inject them a second time. Staging both and committing once
    # closes that window: either the message and the bind land together, or
    # neither does.
    user_message_row = stage_message(db, conversation_id, MessageRole.USER, user_content)
    if pending_attachments:
        # Bind AFTER the message exists (still pre-commit), so an attachment
        # is never orphaned against a message that failed to persist -- and
        # so it can never be injected into a second turn.
        from app.multimodal import service as attachments

        attachments.bind_to_message(db, pending_attachments, user_message_row.id)
    # One commit for both the message and the binding.
    db.commit()
    user_key = principal.user_id if principal.kind == "user" else (conv.guest_email or str(conversation_id))

    async def event_stream():
        assistant_content: list = []
        run_id: str | None = None
        async for event in run_turn(
            _get_client(), db, principal, conversation_id=conversation_id,
            user_key=user_key, history=history, user_message=payload.content,
            attachment_blocks=attachment_blocks,
        ):
            if event.type == "token":
                assistant_content.append({"type": "text", "text": event.data["text"]})
            if event.type == "done":
                run_id = event.data.get("run_id")
            yield f"data: {json.dumps({'type': event.type, **event.data})}\n\n"
        if assistant_content:
            append_message(db, conversation_id, MessageRole.ASSISTANT, assistant_content, run_id=uuid.UUID(run_id) if run_id else None)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
