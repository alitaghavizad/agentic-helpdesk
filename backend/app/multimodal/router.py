"""Upload and retrieval (spec 14). HTTP and authorization only -- validation
lives in validation.py and persistence in service.py.

Both routes are plain `def`, not `async def`. Uploading parses synchronously,
which for a large PDF or an audio file is a multi-second blocking call;
Starlette runs a sync endpoint in a threadpool, so that cannot stall the
event loop and therefore cannot stall the notification SSE streams. Making
these async would silently reintroduce that.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from app.chat.service import get_conversation
from app.db.models import Attachment, Conversation, Role
from app.deps import CurrentPrincipal, DbSession
from app.multimodal import gemini, service, validation

router = APIRouter(tags=["attachments"])

_CHUNK = 64 * 1024


class AttachmentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    size_bytes: int
    kind: str
    parse_status: str
    parse_error: str | None


def _serialize(row: Attachment) -> AttachmentResponse:
    return AttachmentResponse(
        id=str(row.id), filename=row.filename, mime_type=row.mime_type,
        size_bytes=row.size_bytes, kind=row.kind.value,
        parse_status=row.parse_status.value, parse_error=row.parse_error,
    )


def _read_capped(upload: UploadFile) -> bytes:
    """Reads chunk by chunk and stops the moment the cap is exceeded.

    Reading the whole body and checking the length afterwards would let a
    client send a gigabyte before anything objected -- the check has to happen
    while the bytes are arriving, not after."""
    buffer = bytearray()
    while True:
        chunk = upload.file.read(_CHUNK)
        if not chunk:
            break
        buffer.extend(chunk)
        if len(buffer) > validation.MAX_BYTES:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"file exceeds the {validation.MAX_BYTES // (1024 * 1024)} MB limit",
            )
    return bytes(buffer)


@router.post(
    "/api/conversations/{conversation_id}/attachments",
    response_model=AttachmentResponse,
    status_code=status.HTTP_201_CREATED,
)
def upload_attachment(
    conversation_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession,
    file: UploadFile = File(...),
) -> AttachmentResponse:
    if not gemini.is_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "attachment parsing is unavailable: no Gemini API key is configured",
        )

    conversation = get_conversation(db, principal, conversation_id)
    if conversation is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such conversation")

    data = _read_capped(file)
    try:
        row = service.store_and_parse(
            db, conversation_id=conversation_id,
            uploader_user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None,
            filename=file.filename or "attachment",
            declared_mime=file.content_type or "application/octet-stream",
            data=data,
        )
    except validation.RejectedUpload as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, exc.reason)
    db.commit()
    db.refresh(row)
    return _serialize(row)


@router.get("/api/attachments/{attachment_id}")
def get_attachment(
    attachment_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession,
) -> Response:
    row = db.query(Attachment).filter(Attachment.id == attachment_id).one_or_none()
    if row is None or not _may_read(db, principal, row):
        # 404 in both cases, so the endpoint never confirms an id exists.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such attachment")
    return Response(content=service.load_bytes(row), media_type=row.mime_type)


def _may_read(db, principal, row: Attachment) -> bool:
    if principal.role == Role.ADMIN.value:
        return True
    conversation = db.query(Conversation).filter(Conversation.id == row.conversation_id).one_or_none()
    if conversation is None:
        return False
    if principal.kind == "user" and conversation.user_id is not None:
        return str(conversation.user_id) == principal.user_id
    if principal.kind == "guest" and conversation.guest_email:
        # The guest identity comes from the verified JWT, never from a
        # request parameter (spec 6.1).
        return conversation.guest_email == principal.guest_email
    return False
