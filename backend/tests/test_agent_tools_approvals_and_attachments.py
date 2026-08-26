from __future__ import annotations

from app.agent.tools.approvals_and_attachments import (
    CreateApprovalRequestArgs,
    RequestAttachmentArgs,
    create_approval_request_handler,
    request_attachment_handler,
)
from app.db.models import Conversation
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
    args = RequestAttachmentArgs(kind="image", reason="Need to see the error dialog.")
    result = await request_attachment_handler(_GUEST, db_session, args)
    assert result == {"attachment_requested": True, "kind": "image", "reason": "Need to see the error dialog."}
