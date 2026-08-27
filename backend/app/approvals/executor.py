"""Executes an approved action (spec 9.2).

Lives in app/approvals/ rather than app/agent/ -- layout section 16 lists
executor.py under agent/, but section 9.2 says `approvals.execute()`
dispatches to it, and it executes admin-approved actions, not agent tool
calls. Amendment 2.1 of the phase 6 design records the deviation.

Three of the seven action types have no target in this system: there is no
external IT infrastructure to grant access on, no credential store to reset
against, and no external API to write to. Those record an explicitly
simulated result, so nothing downstream can read a simulation as a real
grant. The other four act for real, against our own data.

Everything here is synchronous. `tracing.span`'s context-manager form is
async-only, so the decorator form is used, and the whole path is reached
from a sync FastAPI endpoint that Starlette runs in a threadpool -- which
is also what keeps a blocking 10-second SMTP send from stalling every open
SSE stream.
"""
from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.audit.service import record_audit
from app.db.models import (
    ActorType, ApprovalActionType, ApprovalRequest, Clearance, Conversation, MessageRole, SpanKind, Ticket, User,
)
from app.rbac.policy import Deny, Principal, authorize
from app.tracing.spans import span


@dataclass
class ExecutionOutcome:
    ok: bool
    result: dict


# ---- payload schemas ---------------------------------------------------
# Re-validated at execution time, not merely at request time: spec 9.2's
# "if the payload changed ... execution fails with a recorded reason".

class SendEmailPayload(BaseModel):
    to_address: str
    subject: str
    body: str


class GrantSystemAccessPayload(BaseModel):
    system: str
    target_username: str
    access_level: str


class ResetCredentialPayload(BaseModel):
    target_username: str
    credential_kind: str


class UpdateUserClearancePayload(BaseModel):
    target_username: str
    new_clearance: str


class DiscloseRestrictedInformationPayload(BaseModel):
    disclosure: str


class CrossDepartmentTicketAssignmentPayload(BaseModel):
    # uuid.UUID, not str: a malformed id must fail re-validation as
    # `payload_invalid`, the accurate reason, rather than reach the handler
    # and blow up as `handler_failed`.
    ticket_id: _uuid.UUID
    assignee_helpdesk_ref: str
    rationale: str


class ExternalApiWritePayload(BaseModel):
    endpoint: str
    method: str
    payload: dict


PAYLOAD_SCHEMAS: dict[ApprovalActionType, type[BaseModel]] = {
    ApprovalActionType.SEND_EMAIL: SendEmailPayload,
    ApprovalActionType.GRANT_SYSTEM_ACCESS: GrantSystemAccessPayload,
    ApprovalActionType.RESET_CREDENTIAL: ResetCredentialPayload,
    ApprovalActionType.UPDATE_USER_CLEARANCE: UpdateUserClearancePayload,
    ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION: DiscloseRestrictedInformationPayload,
    ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT: CrossDepartmentTicketAssignmentPayload,
    ApprovalActionType.EXTERNAL_API_WRITE: ExternalApiWritePayload,
}


# ---- simulated handlers ------------------------------------------------

def _simulate(absent_system: str):
    def handler(db: Session, approval: ApprovalRequest, payload: BaseModel) -> dict:
        return {
            "simulated": True,
            "absent_system": absent_system,
            "would_have": payload.model_dump(),
        }
    return handler


Handler = Callable[[Session, ApprovalRequest, BaseModel], dict]


# ---- real handlers ------------------------------------------------------

def _handle_send_email(db: Session, approval: ApprovalRequest, payload: SendEmailPayload) -> dict:
    """Imported here rather than at module scope: app.notifications.email
    imports app.config, and a module-scope import would make importing the
    executor require SMTP configuration to be present."""
    from app.notifications import email as email_module

    row = email_module.send(
        db, approval=approval, to_address=payload.to_address,
        subject=payload.subject, body=payload.body,
    )
    return {
        "email_id": str(row.id),
        "email_status": row.status.value,
        "to_address": row.to_address,
        "smtp_response": row.smtp_response,
    }


def _handle_update_user_clearance(db: Session, approval: ApprovalRequest, payload: UpdateUserClearancePayload) -> dict:
    """Design spec: this action writes users.clearance and is audited. The
    audit row records the mutation itself, distinct from decide()'s own
    audit entry for the approval decision."""
    target = db.query(User).filter(User.username == payload.target_username).one_or_none()
    if target is None:
        raise LookupError(f"no user named {payload.target_username!r}")
    previous = target.clearance.value if target.clearance else None
    target.clearance = Clearance(payload.new_clearance)
    db.flush()
    record_audit(
        db,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        action="user.clearance_changed",
        target_type="user",
        target_id=str(target.id),
        payload={
            "previous_clearance": previous,
            "new_clearance": target.clearance.value,
            "approval_request_id": str(approval.id),
        },
    )
    return {
        "target_username": target.username,
        "previous_clearance": previous,
        "new_clearance": target.clearance.value,
    }


def _handle_disclose_restricted_information(
    db: Session, approval: ApprovalRequest, payload: DiscloseRestrictedInformationPayload,
) -> dict:
    """Spec 9.2: the disclosure is delivered to the user as a system message
    in the conversation, attributed to the approving admin. Attribution is
    the point -- an unattributed disclosure is indistinguishable from the
    agent having decided to answer on its own."""
    from app.chat.service import append_message

    admin_name = "an administrator"
    if approval.decided_by_user_id:
        admin = db.query(User).filter(User.id == approval.decided_by_user_id).one_or_none()
        if admin:
            admin_name = admin.username

    text = (
        f"Approved disclosure (REQ-{approval.request_number:06d}), released by {admin_name}:\n\n"
        f"{payload.disclosure}"
    )
    message = append_message(
        db, approval.conversation_id, MessageRole.SYSTEM, [{"type": "text", "text": text}],
    )
    return {"message_id": str(message.id), "attributed_to": admin_name}


def _handle_cross_department_ticket_assignment(
    db: Session, approval: ApprovalRequest, payload: CrossDepartmentTicketAssignmentPayload,
) -> dict:
    from app.tickets.service import reassign

    ticket = db.query(Ticket).filter(Ticket.id == payload.ticket_id).one_or_none()
    if ticket is None:
        raise LookupError(f"no ticket with id {payload.ticket_id}")

    previous = ticket.assignee_helpdesk_ref
    # Deliberately does NOT notify here. Task 9 makes tickets.service.reassign
    # the single owner of the TICKET_ASSIGNED trigger, so every reassignment
    # notifies exactly once regardless of who initiated it. Emitting here as
    # well would send the assignee two identical notifications.
    reassign(db, ticket, assignee_helpdesk_ref=payload.assignee_helpdesk_ref, rationale=payload.rationale)
    db.flush()
    return {
        "ticket_id": str(ticket.id),
        "previous_assignee": previous,
        "new_assignee": ticket.assignee_helpdesk_ref,
    }


HANDLERS: dict[ApprovalActionType, Handler] = {
    ApprovalActionType.SEND_EMAIL: _handle_send_email,
    ApprovalActionType.UPDATE_USER_CLEARANCE: _handle_update_user_clearance,
    ApprovalActionType.DISCLOSE_RESTRICTED_INFORMATION: _handle_disclose_restricted_information,
    ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT: _handle_cross_department_ticket_assignment,
    ApprovalActionType.GRANT_SYSTEM_ACCESS: _simulate("identity provider / target system"),
    ApprovalActionType.RESET_CREDENTIAL: _simulate("credential store"),
    ApprovalActionType.EXTERNAL_API_WRITE: _simulate("external API"),
}


# ---- re-validation and re-authorization (spec 9.2) ---------------------

def _rebuild_principal(db: Session, approval: ApprovalRequest) -> Principal | ExecutionOutcome:
    """Rebuilds the requester's principal from CURRENT database state, never
    from anything captured when the request was filed. A user demoted or
    deactivated between filing and approval must not have the action run on
    their behalf."""
    if approval.requester_user_id is None:
        # Spec: rebuild the guest principal FROM THE CONVERSATION. Guests are
        # deliberately not rows in `users` (spec 5.1), so the conversation is
        # the only place their identity lives.
        conversation = db.query(Conversation).filter(
            Conversation.id == approval.conversation_id,
        ).one_or_none()
        return Principal(
            kind="guest", user_id=None, role="guest", clearance=None,
            department=None, employee_ref=None, helpdesk_ref=None,
            guest_name=conversation.guest_name if conversation else None,
            guest_email=conversation.guest_email if conversation else None,
        )

    user = db.query(User).filter(User.id == approval.requester_user_id).one_or_none()
    if user is None:
        return ExecutionOutcome(False, {"reason": "requester_not_found", "detail": str(approval.requester_user_id)})
    if not user.is_active:
        return ExecutionOutcome(False, {"reason": "requester_not_active", "detail": user.username})

    return Principal(
        kind="user", user_id=str(user.id), role=user.role.value,
        clearance=user.clearance.value if user.clearance else None,
        department=user.department, employee_ref=user.employee_ref,
        helpdesk_ref=user.helpdesk_ref,
    )


def execute(db: Session, approval: ApprovalRequest) -> ExecutionOutcome:
    """Deliberately NOT decorated with @span. `tracing.spans._ActiveSpan.enter`
    raises RuntimeError when there is no active run, so a decorated `execute`
    would be uncallable from a unit test that has no reason to own a run.
    `execute_traced` below is the production entry point and carries the
    span spec 9.2 requires; this function is the logic, testable on its own."""
    principal_or_failure = _rebuild_principal(db, approval)
    if isinstance(principal_or_failure, ExecutionOutcome):
        return principal_or_failure
    principal = principal_or_failure

    schema = PAYLOAD_SCHEMAS[approval.action_type]
    try:
        payload = schema.model_validate(approval.action_payload)
    except ValidationError as exc:
        # A field-by-field "loc: msg" string, not exc.errors() (a caller
        # greps `detail` for a field name via substring containment, which a
        # list of error dicts does not support) and not str(exc) (pydantic's
        # default rendering embeds a https://errors.pydantic.dev/... doc
        # link per error, which then gets persisted into execution_result).
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_url=False)
        )
        return ExecutionOutcome(False, {"reason": "payload_invalid", "detail": detail})

    # Spec 9.2 requires re-running policy for the original requester. Be
    # honest about what this currently buys: rbac.authorize is role-based
    # only -- it ignores its `arguments` parameter, and
    # create_approval_request is not in _GUEST_DENIED_TOOLS -- so this
    # catches a requester whose ROLE changed and nothing finer. The real
    # protection above is the reload-and-revalidate. This step is here
    # because the spec requires it and because it starts doing genuine work
    # the moment argument-level rules land. Do not describe it as a
    # payload-level policy check.
    decision = authorize(principal, "create_approval_request", dict(approval.action_payload))
    if isinstance(decision, Deny):
        return ExecutionOutcome(False, {"reason": "policy_denied", "detail": decision.reason})

    handler = HANDLERS[approval.action_type]
    try:
        result = handler(db, approval, payload)
    except Exception as exc:  # noqa: BLE001 -- recorded on the approval, never raised at the admin
        return ExecutionOutcome(False, {"reason": "handler_failed", "detail": f"{type(exc).__name__}: {exc}"})

    # An email that did not leave the building is a failed execution, not a
    # successful one that quietly sent nothing.
    if result.get("email_status") == "failed":
        return ExecutionOutcome(False, result)
    return ExecutionOutcome(True, result)


@span(SpanKind.EXECUTOR, "approval.execute")
def execute_traced(db: Session, approval: ApprovalRequest) -> ExecutionOutcome:
    """The production entry point: identical to `execute` but wrapped in the
    `executor` span spec 9.2 requires. Requires an active run -- `decide()`
    starts one. The sync decorator form is used because `span`'s
    context-manager form is async-only and this whole path is sync."""
    return execute(db, approval)
