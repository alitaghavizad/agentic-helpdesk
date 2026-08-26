from __future__ import annotations

import uuid

from app.approvals.service import create
from app.db.models import ApprovalActionType, Conversation, RiskLevel


def test_create_inserts_pending_approval_request(db_session):
    conv = Conversation(guest_name="Guest", guest_email="g@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    request = create(
        db_session, conversation_id=conv.id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.SEND_EMAIL, action_payload={"to": "hr@northstar.example"},
        justification="User needs a copy of their offer letter.", risk_level=RiskLevel.LOW,
        agent_summary="Requesting email send to HR on user's behalf.",
    )

    assert request.request_number is not None
    assert request.status.value == "pending"
