from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from app.db.models import Ticket, TicketStatus
from app.deps import CurrentPrincipal, DbSession
from app.tickets.scoping import can_read_ticket, scope_tickets_query

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
