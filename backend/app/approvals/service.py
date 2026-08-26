from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import ApprovalActionType, ApprovalRequest, ApprovalStatus, RiskLevel


def create(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    task_id: uuid.UUID | None,
    requester_user_id: uuid.UUID | None,
    action_type: ApprovalActionType | str,
    action_payload: dict,
    justification: str,
    risk_level: RiskLevel | str,
    agent_summary: str,
) -> ApprovalRequest:
    """Files a request; the action itself never executes here or from any
    code this phase builds (spec section 9.2 -- decide()/execute() are
    Phase 6's job, and executor.py does not exist yet)."""
    request = ApprovalRequest(
        conversation_id=conversation_id,
        task_id=task_id,
        requester_user_id=requester_user_id,
        action_type=ApprovalActionType(action_type) if not isinstance(action_type, ApprovalActionType) else action_type,
        action_payload=action_payload,
        justification=justification,
        risk_level=RiskLevel(risk_level) if not isinstance(risk_level, RiskLevel) else risk_level,
        agent_summary=agent_summary,
        status=ApprovalStatus.PENDING,
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    return request
