from __future__ import annotations

import uuid

from app.agent.tools.approvals_and_attachments import (
    CreateApprovalRequestArgs,
    RequestAttachmentArgs,
    create_approval_request_handler,
    request_attachment_handler,
)
from app.db.models import Conversation, Notification, NotificationType, Role, User
from app.rbac.policy import Principal

_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)


async def test_create_approval_request_handler_files_a_pending_request(db_session):
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    args = CreateApprovalRequestArgs(
        action_type="send_email", action_payload={"to": "hr@northstar.example"},
        justification="Needs a copy of their offer letter.", risk_level="low",
        agent_summary="Requesting email send.",
    )
    result = await create_approval_request_handler(_GUEST, db_session, args, conversation_id=conv.id)
    assert result["status"] == "pending"
    assert "request_number" in result


async def test_request_attachment_handler_returns_signal_without_touching_db(db_session):
    """A guest has no `users` row to notify -- notify() returns None for a
    None user_id (spec 5.1), so this stays a pure signal with no DB write."""
    args = RequestAttachmentArgs(kind="image", reason="Need to see the error dialog.")
    result = await request_attachment_handler(_GUEST, db_session, args)
    assert result == {"attachment_requested": True, "kind": "image", "reason": "Need to see the error dialog."}


async def test_request_attachment_handler_notifies_a_signed_in_user(db_session):
    """A signed-in user's request survives them closing the tab (Task 9's
    attachment_requested trigger). conversation_id is threaded through via
    dispatch_tool's extra_context mechanism, not model-suppliable input."""
    user = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(user)
    db_session.flush()
    conv = Conversation(user_id=user.id, title="t")
    db_session.add(conv)
    db_session.flush()

    principal = Principal(
        kind="user", user_id=str(user.id), role="employee", clearance="standard",
        department="Engineering", employee_ref="EMP-1", helpdesk_ref=None,
    )
    args = RequestAttachmentArgs(kind="image", reason="Need to see the error dialog.")
    result = await request_attachment_handler(principal, db_session, args, conversation_id=conv.id)
    assert result == {"attachment_requested": True, "kind": "image", "reason": "Need to see the error dialog."}

    notes = db_session.query(Notification).filter(
        Notification.user_id == user.id, Notification.type == NotificationType.ATTACHMENT_REQUESTED,
    ).all()
    assert len(notes) == 1
    assert notes[0].link_type == "conversation"
    assert notes[0].link_id == conv.id
