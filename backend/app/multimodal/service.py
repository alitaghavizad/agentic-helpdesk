"""Owns every write to `attachments` and the orchestration around it
(spec 11): validate -> store -> parse -> row.

Parsing is synchronous here, inside the upload request. The user has just
chosen to upload a file and expects to wait for it, and by the time they
send their next message the extracted content is guaranteed to be present.
The alternative -- parsing in the background -- means the next turn can fire
before extraction finishes, which is a race with no good resolution.

Stages and flushes; it does NOT commit. The caller commits, so an upload
that fails partway leaves neither a row nor an orphaned reference. The file
on disk is written before the row, and is content-addressed, so a crash
between the two leaves an unreferenced blob rather than a row pointing at
nothing -- the harmless direction to fail in.
"""
from __future__ import annotations

import hashlib
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from app.config import get_settings
from app.db.models import Attachment, ParseStatus
from app.multimodal import gemini, validation
from app.tracing.redaction import redact


def storage_root() -> Path:
    root = Path(get_settings().attachment_storage_dir)
    if not root.is_absolute():
        root = Path(__file__).resolve().parents[3] / root
    return root


def load_bytes(attachment: Attachment) -> bytes:
    return (storage_root() / attachment.storage_path).read_bytes()


def store_and_parse(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    uploader_user_id: uuid.UUID | None,
    filename: str,
    declared_mime: str,
    data: bytes,
) -> Attachment:
    """Raises validation.RejectedUpload for anything that fails the boundary
    checks, having written nothing. A parse failure is NOT raised -- it is
    recorded on the row, because a file we cannot read is still a file the
    user sent us."""
    safe_name = validation.sanitize_filename(filename)
    _extension, kind = validation.validate(
        filename=filename, declared_mime=declared_mime, head=data[:64],
    )

    sha256 = hashlib.sha256(data).hexdigest()
    relpath = validation.storage_relpath(sha256)
    destination = storage_root() / relpath
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not destination.exists():
        # Write-then-rename rather than writing in place. Storage is
        # content-addressed, so a truncated file from an interrupted write
        # would be pinned forever: every later upload of those bytes sees the
        # path exists and skips it. os.replace is atomic on Windows and POSIX.
        temporary = destination.with_suffix(".partial")
        temporary.write_bytes(data)
        os.replace(temporary, destination)

    row = Attachment(
        conversation_id=conversation_id,
        message_id=None,
        uploader_user_id=uploader_user_id,
        filename=safe_name,
        mime_type=validation.ALLOWED[_extension][0],
        size_bytes=len(data),
        sha256=sha256,
        storage_path=relpath,
        kind=kind,
        parse_status=ParseStatus.PENDING,
        # Set explicitly rather than left to the column's server_default.
        # Postgres's now() is the TRANSACTION start time, so several
        # attachments uploaded in one request would share a timestamp and the
        # ordering below would fall through to a random uuid4 -- injecting a
        # user's files into their turn in an arbitrary order.
        created_at=datetime.now(timezone.utc),
    )

    # Scoped to THIS conversation, not global. Matching on sha256 alone would
    # save a little more but leaks existence across tenants through timing: a
    # cache hit returns instantly where a fresh parse takes seconds, so anyone
    # holding a file's bytes could learn whether another user had ever
    # uploaded it. A conversation has a single owner, so scoping here removes
    # the cross-tenant channel entirely and still catches the case that
    # actually recurs -- the same file sent twice in one conversation.
    previous = db.query(Attachment).filter(
        Attachment.conversation_id == conversation_id,
        Attachment.sha256 == sha256,
        Attachment.parse_status == ParseStatus.PARSED,
    ).first()
    if previous is not None:
        row.parsed_text = previous.parsed_text
        row.parse_model = previous.parse_model
        row.parse_status = ParseStatus.PARSED
    else:
        try:
            result = gemini.parse(data, mime_type=row.mime_type, kind=kind)
        except gemini.GeminiUnavailable as exc:
            row.parse_status = ParseStatus.FAILED
            row.parse_error = str(exc)
        else:
            # Spec 12.4 -- parsed_text takes the same redaction path as span
            # input/output, so a screenshot of a terminal showing an API key
            # does not persist that key.
            row.parsed_text = redact(result.text)
            row.parse_model = result.model
            row.parse_status = ParseStatus.PARSED

    db.add(row)
    db.flush()
    return row


def pending_for_conversation(db: Session, conversation_id: uuid.UUID) -> list[Attachment]:
    """Parsed but not yet bound to a message. A failed parse is deliberately
    excluded: there is nothing to inject, and an untrusted_data wrapper around
    an empty string is noise the model has to reason about."""
    return db.query(Attachment).filter(
        Attachment.conversation_id == conversation_id,
        Attachment.message_id.is_(None),
        Attachment.parse_status == ParseStatus.PARSED,
    ).order_by(Attachment.created_at.asc(), Attachment.id.asc()).all()


def bind_to_message(db: Session, attachments: list[Attachment], message_id: uuid.UUID) -> None:
    """Binding is what makes injection exactly-once, and it also ties the file
    permanently to the message it accompanied."""
    for attachment in attachments:
        attachment.message_id = message_id
    db.flush()
