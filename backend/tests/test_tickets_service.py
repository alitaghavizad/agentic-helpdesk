from __future__ import annotations

import uuid

import pytest

from app.db.models import Conversation, ResolutionPath, Run, RunStatus, RunTrigger, TaskCategory, TicketPriority
from app.tickets.service import create_ticket, record_task


def _make_conversation(db_session):
    conv = Conversation(guest_name="Guest", guest_email="guest@example.com")
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def _make_run(db_session) -> uuid.UUID:
    """Created directly via db_session rather than tracing.start_run()/end_run()
    so it lives in the same savepoint transaction as the Task/Ticket rows and
    rolls back automatically -- avoids a real Postgres FK-lock deadlock between
    db_session's held transaction and cleanup_run's separate-connection DELETE."""
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run.id


def test_record_task_creates_row_with_pending_resolution_path(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)

    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="Can't connect to VPN", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="User reports VPN client fails to connect.", affected_systems=["vpn"],
        evidence={"error": "timeout"}, classified_by_run_id=run_id,
    )

    assert task.id is not None
    assert task.conversation_id == conv.id
    assert task.resolution_path == ResolutionPath.PENDING
    assert task.category == TaskCategory.VPN_NETWORK


def test_record_task_raises_for_invalid_severity_without_touching_db(db_session):
    """Guards against a model-supplied severity reaching db.commit() as a raw
    string: an invalid Postgres enum value fails at flush, not at this
    boundary, leaving the session unusable for the rest of the turn unless
    rejected here first (mirrors the existing category coercion)."""
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)

    with pytest.raises(ValueError):
        record_task(
            db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
            title="Can't connect to VPN", category=TaskCategory.VPN_NETWORK, severity="not-a-real-severity",
            summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
        )

    # Session must still be usable -- proves the raise happened before any
    # flush/commit reached the DB, not after a poisoned transaction.
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="Can't connect to VPN", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )
    assert task.id is not None


def test_create_ticket_succeeds_for_valid_task(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-001",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="Best semantic match for VPN category.",
        matched_specialization="Network and VPN Support", assignment_score=0.87,
    )

    assert ticket.ticket_number is not None
    assert ticket.task_id == task.id
    assert ticket.status.value == "open"


def test_create_ticket_raises_for_nonexistent_task(db_session):
    with pytest.raises(ValueError, match="does not exist"):
        create_ticket(
            db_session, task_id=uuid.uuid4(), conversation_id=uuid.uuid4(), requester_user_id=None,
            requester_guest_email="g@example.com", assignee_helpdesk_ref="HD-001",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )


def test_create_ticket_raises_when_task_belongs_to_different_conversation(db_session):
    conv = _make_conversation(db_session)
    other_conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="t", category=TaskCategory.OTHER, severity="low", summary="s",
        affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    with pytest.raises(ValueError, match="does not belong"):
        create_ticket(
            db_session, task_id=task.id, conversation_id=other_conv.id, requester_user_id=None,
            requester_guest_email=other_conv.guest_email, assignee_helpdesk_ref="HD-001",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )


def test_create_ticket_raises_when_task_already_has_a_ticket(db_session):
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="t", category=TaskCategory.OTHER, severity="low", summary="s",
        affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )
    create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-001",
        priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
        matched_specialization="s", assignment_score=0.5,
    )

    with pytest.raises(ValueError, match="already has a ticket"):
        create_ticket(
            db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
            requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-002",
            priority=TicketPriority.LOW, title="t", body="b", assignment_rationale="r",
            matched_specialization="s", assignment_score=0.5,
        )


def test_create_ticket_resolves_assignee_user_id_from_the_helpdesk_ref(db_session):
    from app.db.models import EscalationAuthority, Role, User

    specialist = User(
        username="hd-950", email="hd-950@northstar.example", full_name="HD-950", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-950", specialization="Network and VPN Support",
        escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(specialist)
    db_session.commit()

    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-950",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="Best semantic match.", matched_specialization="Network and VPN Support",
        assignment_score=0.87,
    )

    assert ticket.assignee_user_id == specialist.id


def test_create_ticket_leaves_assignee_user_id_null_for_an_unknown_ref(db_session):
    """Spec 8.3 lists create_ticket's validations exhaustively and the
    assignee ref is deliberately not among them -- the text ref stays the
    source of truth, the FK is a convenience join populated when resolvable."""
    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )

    ticket = create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-DOES-NOT-EXIST",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="r", matched_specialization="s", assignment_score=0.5,
    )

    assert ticket.assignee_user_id is None
    assert ticket.assignee_helpdesk_ref == "HD-DOES-NOT-EXIST"


def test_create_ticket_flips_the_task_resolution_path_to_ticketed(db_session):
    from app.db.models import ResolutionPath

    conv = _make_conversation(db_session)
    run_id = _make_run(db_session)
    task = record_task(
        db_session, conversation_id=conv.id, user_id=None, guest_email=conv.guest_email,
        title="VPN issue", category=TaskCategory.VPN_NETWORK, severity="medium",
        summary="s", affected_systems=[], evidence={}, classified_by_run_id=run_id,
    )
    assert task.resolution_path == ResolutionPath.PENDING

    create_ticket(
        db_session, task_id=task.id, conversation_id=conv.id, requester_user_id=None,
        requester_guest_email=conv.guest_email, assignee_helpdesk_ref="HD-950",
        priority=TicketPriority.MEDIUM, title="VPN issue", body="Full description",
        assignment_rationale="r", matched_specialization="s", assignment_score=0.5,
    )

    db_session.refresh(task)
    assert task.resolution_path == ResolutionPath.TICKETED
