from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.approvals.service import create as create_approval_request
from app.rbac.policy import Principal


class CreateApprovalRequestArgs(BaseModel):
    action_type: str
    action_payload: dict
    justification: str
    risk_level: str
    agent_summary: str
    task_id: str | None = None


class RequestAttachmentArgs(BaseModel):
    kind: str
    reason: str


async def create_approval_request_handler(
    principal: Principal, db: Session, args: CreateApprovalRequestArgs, *, conversation_id: uuid.UUID,
) -> dict:
    request = create_approval_request(
        db,
        conversation_id=conversation_id,
        task_id=uuid.UUID(args.task_id) if args.task_id else None,
        requester_user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None,
        action_type=args.action_type, action_payload=args.action_payload,
        justification=args.justification, risk_level=args.risk_level, agent_summary=args.agent_summary,
    )
    return {"request_number": f"REQ-{request.request_number:06d}", "status": request.status.value}


async def request_attachment_handler(principal: Principal, db: Session, args: RequestAttachmentArgs) -> dict:
    """Emits a structured signal for the SSE layer (Task 12) to turn into an
    `attachment_request` event; does not block the turn or touch the
    database (spec section 8.3)."""
    return {"attachment_requested": True, "kind": args.kind, "reason": args.reason}
