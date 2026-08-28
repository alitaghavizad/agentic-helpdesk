from __future__ import annotations

import hashlib

import pytest

from app.db.models import AttachmentKind, Conversation, ParseStatus
from app.multimodal import gemini, service, validation

PNG = b"\x89PNG\r\n\x1a\n" + b"deadbeef" * 4


@pytest.fixture()
def storage(tmp_path, monkeypatch):
    """Uploads write real files. Every test points storage at a tmp dir so
    nothing lands in the repository working tree."""
    monkeypatch.setattr(service, "storage_root", lambda: tmp_path)
    return tmp_path


@pytest.fixture()
def conversation(db_session):
    conv = Conversation(guest_name="Guest", guest_email="guest@northstar.example")
    db_session.add(conv)
    db_session.flush()
    return conv


@pytest.fixture()
def parses_ok(monkeypatch):
    calls = []

    def _parse(data, *, mime_type, kind):
        calls.append(kind)
        return gemini.ParseResult(text="Disk is full. Error 0x80070070.", model="gemini-test")

    monkeypatch.setattr(gemini, "parse", _parse)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    return calls


def test_store_and_parse_writes_the_file_and_a_parsed_row(db_session, storage, conversation, parses_ok):
    row = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    assert row.parse_status is ParseStatus.PARSED
    assert row.kind is AttachmentKind.IMAGE
    assert row.sha256 == hashlib.sha256(PNG).hexdigest()
    assert row.size_bytes == len(PNG)
    assert "0x80070070" in row.parsed_text
    assert (storage / validation.storage_relpath(row.sha256)).read_bytes() == PNG


def test_a_rejected_upload_writes_nothing_at_all(db_session, storage, conversation, parses_ok):
    """Spec 4.4: a validation failure returns the reason and persists nothing
    -- no row, and above all no file."""
    from app.db.models import Attachment

    with pytest.raises(validation.RejectedUpload):
        service.store_and_parse(
            db_session, conversation_id=conversation.id, uploader_user_id=None,
            filename="innocent.png", declared_mime="image/png", data=b"%PDF-1.4 not a png",
        )
    assert db_session.query(Attachment).count() == 0
    assert list(storage.rglob("*")) == []


def test_a_parse_failure_is_recorded_and_the_file_is_still_kept(db_session, storage, conversation, monkeypatch):
    """A file we cannot read is not a file we should discard."""
    def _boom(data, *, mime_type, kind):
        raise gemini.GeminiUnavailable("model exploded")

    monkeypatch.setattr(gemini, "parse", _boom)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)

    row = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    assert row.parse_status is ParseStatus.FAILED
    assert "model exploded" in row.parse_error
    assert row.parsed_text is None
    assert (storage / validation.storage_relpath(row.sha256)).exists()


def test_identical_bytes_reuse_the_existing_parse(db_session, storage, conversation, parses_ok):
    """Spec 4.3: same bytes, same extraction -- paying Gemini twice for it is
    pure waste."""
    first = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    second = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="again.png", declared_mime="image/png", data=PNG,
    )
    assert len(parses_ok) == 1, "the second upload must not call Gemini again"
    assert second.parsed_text == first.parsed_text
    assert second.id != first.id


def test_parsed_text_is_redacted_before_persistence(db_session, storage, conversation, monkeypatch):
    """Spec 12.4: parsed_text goes through the same redaction path as spans."""
    def _leaky(data, *, mime_type, kind):
        return gemini.ParseResult(
            text="the key is sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",
            model="gemini-test",
        )

    monkeypatch.setattr(gemini, "parse", _leaky)
    monkeypatch.setattr(gemini, "is_configured", lambda: True)

    row = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    assert "sk-ant-api03-AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA" not in row.parsed_text


def test_pending_and_bind_are_exactly_once(db_session, storage, conversation, parses_ok):
    from app.db.models import Message, MessageRole

    row = service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    pending = service.pending_for_conversation(db_session, conversation.id)
    assert [a.id for a in pending] == [row.id]

    message = Message(conversation_id=conversation.id, role=MessageRole.USER, content=[])
    db_session.add(message)
    db_session.flush()
    service.bind_to_message(db_session, pending, message.id)
    db_session.flush()

    assert service.pending_for_conversation(db_session, conversation.id) == []


def test_a_failed_parse_is_never_pending_for_injection(db_session, storage, conversation, monkeypatch):
    """There is nothing to inject, and injecting an empty block would put an
    untrusted_data wrapper around nothing."""
    monkeypatch.setattr(gemini, "is_configured", lambda: True)
    monkeypatch.setattr(gemini, "parse", lambda *a, **k: (_ for _ in ()).throw(gemini.GeminiUnavailable("no")))

    service.store_and_parse(
        db_session, conversation_id=conversation.id, uploader_user_id=None,
        filename="shot.png", declared_mime="image/png", data=PNG,
    )
    assert service.pending_for_conversation(db_session, conversation.id) == []
