from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.db.models import ResolutionPath, Role, Severity, Task, TaskCategory, Ticket, TicketPriority, User


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
