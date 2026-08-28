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


def _task_in_its_own_conversation(db_session):
    """Task.classified_by_run_id is a NOT NULL FK to runs, so a Task cannot
    be built in isolation -- same chain conftest's make_ticket builds, and
    for the same reason. Returns (conversation, task)."""
    from app.db.models import Run, RunStatus, RunTrigger, Severity, Task, TaskCategory

    conv = Conversation(guest_name="Guest", guest_email="owner@example.com")
    db_session.add(conv)
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()

    task = Task(
        conversation_id=conv.id, user_id=None, guest_email="owner@example.com",
        title="t", category=TaskCategory.VPN_NETWORK, severity=Severity.MEDIUM, summary="s",
        affected_systems=[], evidence={}, classified_by_run_id=run.id,
    )
    db_session.add(task)
    db_session.commit()
    return conv, task


async def test_create_approval_request_handler_accepts_a_task_from_this_conversation(db_session):
    conv, task = _task_in_its_own_conversation(db_session)

    args = CreateApprovalRequestArgs(
        action_type="send_email", action_payload={"to": "hr@northstar.example"},
        justification="j", risk_level="low", agent_summary="a", task_id=str(task.id),
    )
    result = await create_approval_request_handler(_GUEST, db_session, args, conversation_id=conv.id)
    assert result["status"] == "pending"

    from app.db.models import ApprovalRequest
    row = db_session.query(ApprovalRequest).filter(ApprovalRequest.task_id == task.id).one()
    assert row.conversation_id == conv.id


async def test_create_approval_request_handler_rejects_another_conversations_task(db_session):
    """task_id is model-supplied while conversation_id is threaded in by
    dispatch_tool and cannot be influenced by the model. Unchecked, the
    model could attach its approval request to a task belonging to a
    conversation it has nothing to do with. tickets.service.create_ticket
    already refuses exactly this; the two must agree."""
    from app.db.models import ApprovalRequest

    _owner_conv, task = _task_in_its_own_conversation(db_session)
    other = Conversation(guest_name="Guest", guest_email="other@example.com")
    db_session.add(other)
    db_session.commit()

    args = CreateApprovalRequestArgs(
        action_type="send_email", action_payload={"to": "hr@northstar.example"},
        justification="j", risk_level="low", agent_summary="a", task_id=str(task.id),
    )
    result = await create_approval_request_handler(_GUEST, db_session, args, conversation_id=other.id)

    assert result["is_error"] is True
    assert "does not belong to conversation" in result["content"]
    assert db_session.query(ApprovalRequest).filter(ApprovalRequest.task_id == task.id).count() == 0


async def test_create_approval_request_handler_rejects_a_task_that_does_not_exist(db_session):
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()

    args = CreateApprovalRequestArgs(
        action_type="send_email", action_payload={"to": "hr@northstar.example"},
        justification="j", risk_level="low", agent_summary="a", task_id=str(uuid.uuid4()),
    )
    result = await create_approval_request_handler(_GUEST, db_session, args, conversation_id=conv.id)
    assert result["is_error"] is True
    assert "does not exist" in result["content"]


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
