from __future__ import annotations

import uuid

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.approvals.service import create as create_approval_request
from app.db.models import Task
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
    # `task_id` is model-supplied while `conversation_id` is threaded in by
    # dispatch_tool and cannot be influenced by the model, so an unchecked
    # task_id lets one conversation's approval request be filed against
    # another conversation's task -- linking it to work the requester has no
    # part in. tickets.service.create_ticket already rejects exactly this,
    # with these same two messages; the two paths must agree, or the weaker
    # one is simply the way around the stronger.
    task_id = uuid.UUID(args.task_id) if args.task_id else None
    if task_id is not None:
        task = db.get(Task, task_id)
        if task is None:
            return {"is_error": True, "content": f"task {task_id} does not exist"}
        if task.conversation_id != conversation_id:
            return {
                "is_error": True,
                "content": f"task {task_id} does not belong to conversation {conversation_id}",
            }

    request = create_approval_request(
        db,
        conversation_id=conversation_id,
        task_id=task_id,
        requester_user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None,
        action_type=args.action_type, action_payload=args.action_payload,
        justification=args.justification, risk_level=args.risk_level, agent_summary=args.agent_summary,
    )
    return {"request_number": f"REQ-{request.request_number:06d}", "status": request.status.value}


async def request_attachment_handler(
    principal: Principal, db: Session, args: RequestAttachmentArgs, *, conversation_id: uuid.UUID | None = None,
) -> dict:
    """Emits a structured signal for the SSE layer to turn into an
    `attachment_request` event, and -- for a signed-in user -- a durable
    notification so the request survives the user closing the tab. Still
    does not block the turn (spec 8.3)."""
    from app.db.models import NotificationType
    from app.notifications import service as notifications

    notifications.notify(
        db,
        user_id=uuid.UUID(principal.user_id) if principal.kind == "user" and principal.user_id else None,
        type=NotificationType.ATTACHMENT_REQUESTED,
        title=f"The assistant asked for a {args.kind}",
        body=args.reason,
        link_type="conversation",
        link_id=conversation_id,
    )
    return {"attachment_requested": True, "kind": args.kind, "reason": args.reason}
