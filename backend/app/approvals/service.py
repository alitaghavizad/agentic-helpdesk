from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import ApprovalActionType, ApprovalRequest, ApprovalStatus, RiskLevel
from app.rbac.policy import Principal


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
    """Files a request; the action itself never executes here. Spec section
    9.2 assigns execution to decide()/execute() below, in approvals/executor.py."""
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


class NotPending(RuntimeError):
    """A decision was attempted on a request that is not `pending`. This is
    the idempotency guard: without it, a re-submitted approval would execute
    the action a second time."""


def get(db: Session, request_id: uuid.UUID) -> ApprovalRequest | None:
    return db.query(ApprovalRequest).filter(ApprovalRequest.id == request_id).one_or_none()


def list_for_admin(db: Session, *, status: ApprovalStatus | None = None) -> list[ApprovalRequest]:
    query = db.query(ApprovalRequest)
    if status is not None:
        query = query.filter(ApprovalRequest.status == status)
    return query.order_by(ApprovalRequest.created_at.desc()).all()


def decide(
    db: Session,
    principal: Principal,
    request_id: uuid.UUID,
    *,
    approve: bool,
    note: str = "",
) -> ApprovalRequest:
    """Records the admin's decision and, on approval, executes the action
    synchronously (spec 9.2).

    Synchronous on purpose: the admin gets the true terminal status in one
    round trip, the executor span nests under a single run, and no test
    needs to poll. The cost is up to ~10s on a send_email approval, bounded
    by the SMTP timeout -- acceptable for a single deliberate admin action,
    and it does not stall the event loop because the calling endpoint is a
    sync `def` that Starlette runs in a threadpool.

    Commits once, at the end, so the decision, its audit row, its execution
    side effects, and its notification are one atomic unit. A failed
    execution is still committed: `failed` with a recorded reason is the
    correct outcome, not a reason to forget the decision happened.
    """
    from datetime import datetime, timezone

    from app.approvals import executor
    from app.audit.service import record_audit
    from app.db.models import ActorType, NotificationType, RunStatus, RunTrigger
    from app.notifications import service as notifications
    from app.tracing import spans

    request = get(db, request_id)
    if request is None:
        raise LookupError(f"no approval request with id {request_id}")
    if request.status is not ApprovalStatus.PENDING:
        raise NotPending(
            f"approval {request.id} is already {request.status.value!r}; only a pending request can be decided"
        )

    request.status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
    request.decided_by_user_id = uuid.UUID(principal.user_id) if principal.user_id else None
    request.decided_at = datetime.now(timezone.utc)
    request.decision_note = note
    db.flush()

    record_audit(
        db,
        actor_type=ActorType.USER,
        actor_id=principal.user_id,
        action="approval.decide",
        target_type="approval_request",
        target_id=str(request.id),
        payload={"approve": approve, "note": note, "action_type": request.action_type.value},
    )

    if approve:
        # No Phase 6 table has a foreign key to `runs`, so starting a run on
        # tracing's own committed connection cannot deadlock against this
        # session's open transaction. Do not add such an FK later without
        # revisiting this.
        handle = spans.start_run(
            RunTrigger.APPROVAL_EXECUTION,
            conversation_id=request.conversation_id,
            user_id=request.requester_user_id,
        )
        outcome = None
        try:
            outcome = executor.execute_traced(db, request)
        finally:
            spans.end_run(
                handle,
                status=RunStatus.OK if (outcome and outcome.ok) else RunStatus.ERROR,
                error=None if (outcome and outcome.ok) else str((outcome.result if outcome else "executor raised")),
            )

        request.status = ApprovalStatus.EXECUTED if outcome.ok else ApprovalStatus.FAILED
        request.executed_at = datetime.now(timezone.utc)
        request.execution_result = outcome.result
        db.flush()

    verb = {
        ApprovalStatus.DENIED: "denied",
        ApprovalStatus.EXECUTED: "approved and executed",
        ApprovalStatus.FAILED: "approved, but execution failed",
    }[request.status]
    # The verb is embedded in `body`, not just `title`: a notification is
    # read on its own (e.g. from an in-app feed row or a push payload) and
    # must say what happened without depending on the title being shown
    # alongside it. The admin's note, when given, is appended rather than
    # substituted -- otherwise a note like "Not justified." would read as
    # the sole content and never say the request was actually denied.
    body = f"Your request was {verb}."
    if note:
        body = f"{body} {note}"
    notifications.notify(
        db,
        user_id=request.requester_user_id,
        type=NotificationType.APPROVAL_DECIDED,
        title=f"Request REQ-{request.request_number:06d} was {verb}",
        body=body,
        link_type="approval_request",
        link_id=request.id,
    )

    db.commit()
    db.refresh(request)
    return request
