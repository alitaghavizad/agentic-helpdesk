"""Admin endpoints. Phase 6 adds only the two approvals routes from spec 14;
Phase 8 fills in the rest of the panel.

Both routes are `def`, not `async def`, on purpose. Approving a send_email
action performs a blocking SMTP call with a 10-second timeout; Starlette
runs a sync endpoint in a threadpool, so that block cannot stall the event
loop and therefore cannot stall every open notification SSE stream. Making
these async would silently reintroduce that.
"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.approvals import service as approvals
from app.db.models import ApprovalStatus
from app.deps import DbSession, require_role
from app.rbac.policy import Principal

router = APIRouter(prefix="/api/admin", tags=["admin"])

AdminPrincipal = Annotated[Principal, Depends(require_role("admin"))]


class DecideRequest(BaseModel):
    approve: bool
    note: str = ""


class ApprovalResponse(BaseModel):
    id: str
    request_number: str
    conversation_id: str
    action_type: str
    action_payload: dict
    justification: str
    risk_level: str
    agent_summary: str
    status: str
    decision_note: str | None
    execution_result: dict | None


def _serialize(request) -> ApprovalResponse:
    return ApprovalResponse(
        id=str(request.id),
        request_number=f"REQ-{request.request_number:06d}",
        conversation_id=str(request.conversation_id),
        action_type=request.action_type.value,
        action_payload=request.action_payload,
        justification=request.justification,
        risk_level=request.risk_level.value,
        agent_summary=request.agent_summary,
        status=request.status.value,
        decision_note=request.decision_note,
        execution_result=request.execution_result,
    )


@router.get("/approvals", response_model=list[ApprovalResponse])
def list_approvals(principal: AdminPrincipal, db: DbSession, status: str | None = None) -> list[ApprovalResponse]:
    parsed = None
    if status is not None:
        try:
            parsed = ApprovalStatus(status)
        except ValueError:
            raise HTTPException(422, f"unknown approval status {status!r}")
    return [_serialize(r) for r in approvals.list_for_admin(db, status=parsed)]


@router.post("/approvals/{request_id}/decide", response_model=ApprovalResponse)
def decide_approval(
    request_id: uuid.UUID, payload: DecideRequest, principal: AdminPrincipal, db: DbSession,
) -> ApprovalResponse:
    # Check existence explicitly instead of catching LookupError around
    # decide(): KeyError and IndexError are both LookupError subclasses, so
    # `except LookupError` here would also swallow an unrelated bug inside
    # decide() -- e.g. a bad dict/list lookup somewhere in the executor --
    # and misreport it as 404 "no such approval request" instead of letting
    # it surface as a 500 that gets noticed and fixed.
    if approvals.get(db, request_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such approval request")
    try:
        decided = approvals.decide(db, principal, request_id, approve=payload.approve, note=payload.note)
    except approvals.NotPending as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    return _serialize(decided)
