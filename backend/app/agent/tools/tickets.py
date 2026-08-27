from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.models import TaskCategory, Severity, Ticket, TicketPriority, TicketStatus
from app.rbac.policy import Principal
from app.tickets.scoping import can_read_ticket, scope_tickets_query
from app.tickets.service import create_ticket, record_task


class RecordTaskArgs(BaseModel):
    title: str
    category: TaskCategory
    severity: Severity
    summary: str
    affected_systems: list[str]
    evidence: dict = {}


class CreateTicketArgs(BaseModel):
    task_id: str
    assignee_helpdesk_ref: str
    # create_ticket is a strict tool (see app/agent/registry.py's
    # _NON_STRICT_TOOLS docstring), so this is Literal[...] rather than the
    # TicketPriority enum class -- Pydantic renders Literal as an inline
    # `enum` array, while an Enum class field would pull in a $ref/$defs
    # pair that strict mode forbids. Plain `str` used to accept any value,
    # including severity-shaped ones like "critical" (TicketPriority is
    # low/medium/high/urgent; Severity is low/medium/high/critical) -- a
    # model reusing its own severity value for priority passed Pydantic
    # validation here, then failed only inside create_ticket()'s
    # `TicketPriority(priority)` coercion, deep enough that the agent would
    # keep retrying the same bad value until the turn died.
    priority: Literal[tuple(p.value for p in TicketPriority)]
    title: str
    body: str
    assignment_rationale: str
    matched_specialization: str
    assignment_score: float


class ListMyTicketsArgs(BaseModel):
    status: str | None = None


class GetTicketArgs(BaseModel):
    ticket_id: str


async def record_task_handler(
    principal: Principal, db: Session, args: RecordTaskArgs, *,
    conversation_id: uuid.UUID, run_id: uuid.UUID, guest_email: str | None = None,
) -> dict:
    task = record_task(
        db,
        conversation_id=conversation_id,
        user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None,
        guest_email=guest_email if principal.kind == "guest" else None,
        title=args.title, category=args.category, severity=args.severity, summary=args.summary,
        affected_systems=args.affected_systems, evidence=args.evidence, classified_by_run_id=run_id,
    )
    return {"task_id": str(task.id), "category": task.category.value, "resolution_path": task.resolution_path.value}


async def create_ticket_handler(
    principal: Principal, db: Session, args: CreateTicketArgs, *, conversation_id: uuid.UUID, guest_email: str | None = None,
) -> dict:
    try:
        ticket = create_ticket(
            db,
            task_id=uuid.UUID(args.task_id), conversation_id=conversation_id,
            requester_user_id=uuid.UUID(principal.user_id) if principal.kind == "user" else None,
            requester_guest_email=guest_email if principal.kind == "guest" else None,
            assignee_helpdesk_ref=args.assignee_helpdesk_ref, priority=args.priority,
            title=args.title, body=args.body, assignment_rationale=args.assignment_rationale,
            matched_specialization=args.matched_specialization, assignment_score=args.assignment_score,
        )
    except ValueError as exc:
        return {"is_error": True, "content": str(exc)}
    return {
        "ticket_number": f"TCK-{ticket.ticket_number:06d}",
        "status": ticket.status.value,
        "assignee_helpdesk_ref": ticket.assignee_helpdesk_ref,
    }


async def list_my_tickets_handler(
    principal: Principal, db: Session, args: ListMyTicketsArgs, *, guest_email: str | None = None,
) -> dict:
    query = scope_tickets_query(db.query(Ticket), principal, guest_email=guest_email)
    if args.status is not None:
        query = query.filter(Ticket.status == TicketStatus(args.status))
    tickets = query.order_by(Ticket.created_at.desc()).all()
    return {
        "tickets": [
            {"ticket_number": f"TCK-{t.ticket_number:06d}", "title": t.title, "status": t.status.value, "priority": t.priority.value}
            for t in tickets
        ]
    }


async def get_ticket_handler(
    principal: Principal, db: Session, args: GetTicketArgs, *, guest_email: str | None = None,
) -> dict:
    ticket = db.get(Ticket, uuid.UUID(args.ticket_id))
    if ticket is None:
        return {"is_error": True, "content": "no such ticket"}
    if not can_read_ticket(principal, ticket, guest_email=guest_email):
        return {"is_error": True, "content": "you do not have access to this ticket"}
    return {
        "ticket_number": f"TCK-{ticket.ticket_number:06d}", "title": ticket.title, "body": ticket.body,
        "status": ticket.status.value, "priority": ticket.priority.value,
        "assignee_helpdesk_ref": ticket.assignee_helpdesk_ref,
    }
