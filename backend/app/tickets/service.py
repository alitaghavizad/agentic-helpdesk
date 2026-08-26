from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.db.models import (
    ResolutionPath, Role, Severity, Task, TaskCategory, Ticket, TicketPriority, TicketStatus, User,
)


def record_task(
    db: Session,
    *,
    conversation_id: uuid.UUID,
    user_id: uuid.UUID | None,
    guest_email: str | None,
    title: str,
    category: TaskCategory | str,
    severity: Severity | str,
    summary: str,
    affected_systems: list[str],
    evidence: dict,
    classified_by_run_id: uuid.UUID,
) -> Task:
    """Written the moment the agent classifies a problem, before any
    routing decision (spec section 5.3) -- called unconditionally whenever
    the agent recognizes a problem, with no gate of its own."""
    task = Task(
        conversation_id=conversation_id,
        user_id=user_id,
        guest_email=guest_email,
        title=title,
        category=TaskCategory(category) if not isinstance(category, TaskCategory) else category,
        severity=Severity(severity) if not isinstance(severity, Severity) else severity,
        summary=summary,
        affected_systems=affected_systems,
        evidence=evidence,
        classified_by_run_id=classified_by_run_id,
        resolution_path=ResolutionPath.PENDING,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def create_ticket(
    db: Session,
    *,
    task_id: uuid.UUID,
    conversation_id: uuid.UUID,
    requester_user_id: uuid.UUID | None,
    requester_guest_email: str | None,
    assignee_helpdesk_ref: str,
    priority: TicketPriority | str,
    title: str,
    body: str,
    assignment_rationale: str,
    matched_specialization: str,
    assignment_score: float,
) -> Ticket:
    """Validated per spec section 8.3: the task must exist, belong to this
    conversation, and not already have a ticket. matched_specialization and
    assignment_score are not in the spec's minimal illustrative tool-arg
    list (section 8.3) but ARE required, non-nullable Ticket columns
    (section 5.3) -- the model already has both from its immediately-prior
    find_helpdesk_specialist call, so the tool schema (Task 6's
    tools/tickets.py) accepts them as explicit arguments rather than this
    function inventing or re-deriving them."""
    task = db.get(Task, task_id)
    if task is None:
        raise ValueError(f"task {task_id} does not exist")
    if task.conversation_id != conversation_id:
        raise ValueError(f"task {task_id} does not belong to conversation {conversation_id}")
    existing = db.query(Ticket).filter(Ticket.task_id == task_id).first()
    if existing is not None:
        raise ValueError(f"task {task_id} already has a ticket ({existing.id})")

    # Spec 5.3 carries both an assignee_helpdesk_ref text column and an
    # assignee_user_id FK. The ref is the source of truth (it is what
    # routing produced); the FK is a convenience join, populated when it
    # resolves. An unresolvable ref is NOT an error: spec 8.3 lists this
    # function's validations exhaustively and the assignee is not among them.
    assignee = db.query(User).filter(
        User.role == Role.HELPDESK, User.helpdesk_ref == assignee_helpdesk_ref,
    ).one_or_none()

    ticket = Ticket(
        task_id=task_id,
        conversation_id=conversation_id,
        requester_user_id=requester_user_id,
        requester_guest_email=requester_guest_email,
        assignee_helpdesk_ref=assignee_helpdesk_ref,
        assignee_user_id=assignee.id if assignee is not None else None,
        matched_specialization=matched_specialization,
        assignment_rationale=assignment_rationale,
        assignment_score=assignment_score,
        priority=TicketPriority(priority) if not isinstance(priority, TicketPriority) else priority,
        title=title,
        body=body,
    )
    db.add(ticket)
    # The task is no longer merely classified -- it has become a ticket
    # (spec 5.3's resolution_path enum). Same transaction as the insert:
    # a task must never read `ticketed` without its ticket existing.
    task.resolution_path = ResolutionPath.TICKETED
    db.commit()
    db.refresh(ticket)
    return ticket


class InvalidTransition(ValueError):
    """Raised when a caller asks for a status change the lifecycle forbids."""


# Spec 5.3's six statuses as an explicit state machine. Read as
# "from -> the set of statuses reachable from it". CLOSED is terminal;
# RESOLVED can be reopened to IN_PROGRESS because a resolution that did not
# hold is a normal helpdesk outcome, not a data-repair scenario. Every
# TicketStatus must appear as a key -- a test asserts this, so adding a new
# status to the enum cannot silently leave a hole here.
LEGAL_TRANSITIONS: dict[TicketStatus, frozenset[TicketStatus]] = {
    TicketStatus.OPEN: frozenset({
        TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED, TicketStatus.CLOSED,
    }),
    TicketStatus.ASSIGNED: frozenset({
        TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    }),
    TicketStatus.IN_PROGRESS: frozenset({
        TicketStatus.RESOLVED, TicketStatus.ESCALATED, TicketStatus.CLOSED,
    }),
    TicketStatus.ESCALATED: frozenset({
        TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED, TicketStatus.CLOSED,
    }),
    TicketStatus.RESOLVED: frozenset({TicketStatus.CLOSED, TicketStatus.IN_PROGRESS}),
    TicketStatus.CLOSED: frozenset(),
}


def transition_status(db: Session, ticket: Ticket, new_status: TicketStatus | str) -> Ticket:
    """Stages a validated status change. Does NOT commit -- callers commit
    the change together with its audit_log row so the two can never
    disagree. Raises InvalidTransition for a forbidden move and ValueError
    for a status string that is not a TicketStatus at all."""
    target = TicketStatus(new_status) if not isinstance(new_status, TicketStatus) else new_status
    if target not in LEGAL_TRANSITIONS[ticket.status]:
        raise InvalidTransition(
            f"cannot move ticket from {ticket.status.value!r} to {target.value!r}"
        )
    ticket.status = target
    return ticket


def reassign(db: Session, ticket: Ticket, *, assignee_helpdesk_ref: str, rationale: str) -> Ticket:
    """Stages a reassignment, appending to (never overwriting) the rationale
    so the assignment history stays explainable in the dossier (spec 8.4:
    'the rationale string is stored on the ticket so the assignment is
    explainable'). Mirrors create_ticket's rule for an unresolvable ref:
    the text ref is authoritative, the FK is nulled rather than rejected."""
    assignee = db.query(User).filter(
        User.role == Role.HELPDESK, User.helpdesk_ref == assignee_helpdesk_ref,
    ).one_or_none()
    previous = ticket.assignee_helpdesk_ref
    ticket.assignee_helpdesk_ref = assignee_helpdesk_ref
    ticket.assignee_user_id = assignee.id if assignee is not None else None
    if assignee is not None and assignee.specialization:
        ticket.matched_specialization = assignee.specialization
    ticket.assignment_rationale = (
        f"{ticket.assignment_rationale}\nReassigned from {previous} to {assignee_helpdesk_ref}: {rationale}"
    )
    return ticket


def resolve_ticket(
    db: Session, ticket: Ticket, *, resolution: str, resolved_by_user_id: uuid.UUID | None,
) -> Ticket:
    """Stages the resolve transition plus its three resolution columns
    (spec 5.3). Does NOT commit. A blank resolution is rejected -- the
    resolution text is what Phase 9's learning loop later reads, so an
    empty one is worse than no resolution at all."""
    if not resolution or not resolution.strip():
        raise ValueError("resolution must not be empty")
    transition_status(db, ticket, TicketStatus.RESOLVED)
    ticket.resolution = resolution.strip()
    ticket.resolved_by_user_id = resolved_by_user_id
    ticket.resolved_at = datetime.now(timezone.utc)
    return ticket
