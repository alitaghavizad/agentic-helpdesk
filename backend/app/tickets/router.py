from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.audit.service import actor_from_principal, record_audit
from app.db.models import Ticket, TicketPriority, TicketStatus
from app.deps import CurrentPrincipal, DbSession, require_role
from app.rbac.policy import Principal
from app.tickets.scoping import can_read_ticket, scope_tickets_query
from app.tickets.service import InvalidTransition, reassign, resolve_ticket, transition_status

router = APIRouter(prefix="/api/tickets", tags=["tickets"])


class TicketSummary(BaseModel):
    id: str
    ticket_number: str
    title: str
    status: str
    priority: str
    assignee_helpdesk_ref: str
    created_at: str


class TicketDetail(TicketSummary):
    body: str
    matched_specialization: str
    assignment_rationale: str
    assignment_score: float
    resolution: str | None
    resolved_at: str | None


def _number(ticket: Ticket) -> str:
    return f"TCK-{ticket.ticket_number:06d}"


def serialize_summary(ticket: Ticket) -> TicketSummary:
    return TicketSummary(
        id=str(ticket.id), ticket_number=_number(ticket), title=ticket.title,
        status=ticket.status.value, priority=ticket.priority.value,
        assignee_helpdesk_ref=ticket.assignee_helpdesk_ref,
        created_at=ticket.created_at.isoformat(),
    )


def serialize_detail(ticket: Ticket) -> TicketDetail:
    return TicketDetail(
        **serialize_summary(ticket).model_dump(),
        body=ticket.body,
        matched_specialization=ticket.matched_specialization,
        assignment_rationale=ticket.assignment_rationale,
        assignment_score=float(ticket.assignment_score),
        resolution=ticket.resolution,
        resolved_at=ticket.resolved_at.isoformat() if ticket.resolved_at else None,
    )


def load_readable_ticket(db, principal, ticket_id: uuid.UUID) -> Ticket:
    """Spec 6.4 lookup used by every by-id endpoint in this module. Returns
    404 for both 'does not exist' and 'exists but is not yours' -- a 403
    would confirm the existence of tickets the caller may not see."""
    ticket = db.get(Ticket, ticket_id)
    if ticket is None or not can_read_ticket(principal, ticket):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no such ticket")
    return ticket


@router.get("", response_model=list[TicketSummary])
def list_tickets(
    principal: CurrentPrincipal,
    db: DbSession,
    ticket_status: TicketStatus | None = Query(default=None, alias="status"),
) -> list[TicketSummary]:
    query = scope_tickets_query(db.query(Ticket), principal)
    if ticket_status is not None:
        query = query.filter(Ticket.status == ticket_status)
    return [serialize_summary(t) for t in query.order_by(Ticket.created_at.desc()).all()]


@router.get("/{ticket_id}", response_model=TicketDetail)
def get_ticket(ticket_id: uuid.UUID, principal: CurrentPrincipal, db: DbSession) -> TicketDetail:
    return serialize_detail(load_readable_ticket(db, principal, ticket_id))


# Spec 14 marks PATCH and resolve as helpdesk/admin. The role gate is the
# coarse check; load_readable_ticket then applies spec 6.4's row scoping, so
# a helpdesk user still only reaches tickets assigned to them.
StaffPrincipal = Annotated[Principal, Depends(require_role("helpdesk", "admin"))]


class UpdateTicketRequest(BaseModel):
    status: TicketStatus | None = None
    priority: TicketPriority | None = None
    assignee_helpdesk_ref: str | None = None
    reassignment_rationale: str | None = None


@router.patch("/{ticket_id}", response_model=TicketDetail)
def update_ticket(
    ticket_id: uuid.UUID, payload: UpdateTicketRequest, principal: StaffPrincipal, db: DbSession,
) -> TicketDetail:
    ticket = load_readable_ticket(db, principal, ticket_id)

    if payload.status is None and payload.priority is None and payload.assignee_helpdesk_ref is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "no fields to update")
    if payload.assignee_helpdesk_ref is not None and not (payload.reassignment_rationale or "").strip():
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "reassignment_rationale is required when changing the assignee",
        )

    changes: dict[str, dict[str, str | None]] = {}

    if payload.priority is not None and payload.priority != ticket.priority:
        changes["priority"] = {"from": ticket.priority.value, "to": payload.priority.value}
        ticket.priority = payload.priority

    if payload.assignee_helpdesk_ref is not None and payload.assignee_helpdesk_ref != ticket.assignee_helpdesk_ref:
        changes["assignee_helpdesk_ref"] = {"from": ticket.assignee_helpdesk_ref, "to": payload.assignee_helpdesk_ref}
        reassign(
            db, ticket, assignee_helpdesk_ref=payload.assignee_helpdesk_ref,
            rationale=payload.reassignment_rationale.strip(),
        )

    if payload.status is not None and payload.status != ticket.status:
        previous = ticket.status.value
        try:
            transition_status(db, ticket, payload.status)
        except InvalidTransition as exc:
            # 409: the request is well-formed and authorized, but conflicts
            # with the ticket's current state.
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
        changes["status"] = {"from": previous, "to": payload.status.value}

    actor_type, actor_id = actor_from_principal(principal)
    record_audit(
        db, actor_type=actor_type, actor_id=actor_id, action="ticket.update",
        target_type="ticket", target_id=str(ticket.id), payload={"changes": changes},
    )
    # One commit for the mutation and its audit row together -- spec 5.4's
    # append-only log must never describe a change that was rolled back.
    db.commit()
    db.refresh(ticket)
    return serialize_detail(ticket)


class ResolveTicketRequest(BaseModel):
    # min_length=1 catches "", and the service layer rejects whitespace-only
    # text; both paths matter, since a resolution is what Phase 9's learning
    # loop later reads.
    resolution: str = Field(min_length=1)


@router.post("/{ticket_id}/resolve", response_model=TicketDetail)
def resolve_ticket_endpoint(
    ticket_id: uuid.UUID, payload: ResolveTicketRequest, principal: StaffPrincipal, db: DbSession,
) -> TicketDetail:
    ticket = load_readable_ticket(db, principal, ticket_id)

    try:
        resolve_ticket(
            db, ticket, resolution=payload.resolution,
            resolved_by_user_id=uuid.UUID(principal.user_id) if principal.user_id else None,
        )
    except InvalidTransition as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc))
    except ValueError as exc:
        # Whitespace-only resolution: semantically a validation failure, so
        # 422 to match what Pydantic returns for the empty-string case.
        db.rollback()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc))

    actor_type, actor_id = actor_from_principal(principal)
    record_audit(
        db, actor_type=actor_type, actor_id=actor_id, action="ticket.resolve",
        target_type="ticket", target_id=str(ticket.id),
        payload={"resolution": ticket.resolution},
    )
    db.commit()
    db.refresh(ticket)
    return serialize_detail(ticket)
