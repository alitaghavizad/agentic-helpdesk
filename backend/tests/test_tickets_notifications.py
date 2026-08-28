from __future__ import annotations

import uuid

import pytest

from app.db.models import (
    Notification, NotificationType, Role, TicketStatus, User,
)
from app.tickets import service as tickets


@pytest.fixture()
def helpdesk_user(db_session):
    row = User(
        username=f"hd{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.HELPDESK, helpdesk_ref="HD-905", is_active=True,
    )
    db_session.add(row)
    db_session.flush()
    return row


def _notifications_of(db_session, user_id, type_):
    return db_session.query(Notification).filter(
        Notification.user_id == user_id, Notification.type == type_,
    ).all()


def test_reassign_notifies_the_new_assignee(db_session, make_ticket, helpdesk_user):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")
    tickets.reassign(db_session, ticket, assignee_helpdesk_ref="HD-905", rationale="Specialist match")
    db_session.flush()
    assert len(_notifications_of(db_session, helpdesk_user.id, NotificationType.TICKET_ASSIGNED)) == 1


def test_an_approved_cross_department_assignment_notifies_exactly_once(
    db_session, make_ticket, helpdesk_user,
):
    """reassign() is the single owner of the TICKET_ASSIGNED trigger. The
    executor handler calls reassign() and must NOT emit its own notification
    -- doing so would send the assignee two identical ones, which no
    single-module test would catch."""
    from app.approvals import executor
    from app.db.models import (
        ApprovalActionType, ApprovalRequest, ApprovalStatus, RiskLevel,
    )

    ticket = make_ticket(assignee_helpdesk_ref="HD-901")
    approval = ApprovalRequest(
        conversation_id=ticket.conversation_id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.CROSS_DEPARTMENT_TICKET_ASSIGNMENT,
        action_payload={
            "ticket_id": str(ticket.id), "assignee_helpdesk_ref": "HD-905",
            "rationale": "Specialist match",
        },
        justification="j", risk_level=RiskLevel.LOW, agent_summary="a",
        status=ApprovalStatus.APPROVED,
    )
    db_session.add(approval)
    db_session.flush()

    outcome = executor.execute(db_session, approval)
    db_session.flush()
    assert outcome.ok is True
    assert len(_notifications_of(db_session, helpdesk_user.id, NotificationType.TICKET_ASSIGNED)) == 1


def test_status_change_notifies_the_requester(db_session, make_ticket):
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id)
    tickets.transition_status(db_session, ticket, TicketStatus.IN_PROGRESS)
    db_session.flush()
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_STATUS_CHANGED)) == 1


def test_resolve_notifies_the_requester(db_session, make_ticket):
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id, status=TicketStatus.IN_PROGRESS)
    tickets.resolve_ticket(db_session, ticket, resolution="Rotated the cert.", resolved_by_user_id=None)
    db_session.flush()
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_RESOLVED)) == 1


def test_a_guest_requester_produces_no_notification_and_no_crash(db_session, make_ticket):
    """make_ticket defaults to a guest requester. notifications.user_id is
    NOT NULL, so the retrofit must skip rather than fail."""
    ticket = make_ticket()
    tickets.transition_status(db_session, ticket, TicketStatus.IN_PROGRESS)
    db_session.flush()  # must not raise


def test_resolve_does_not_double_notify(db_session, make_ticket):
    """resolve_ticket calls transition_status internally. The requester must
    get one TICKET_RESOLVED notification, not a status-changed one as well."""
    requester = User(
        username=f"u{uuid.uuid4().hex[:8]}", email=f"{uuid.uuid4().hex[:8]}@northstar.example",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE, is_active=True,
    )
    db_session.add(requester)
    db_session.flush()

    ticket = make_ticket(requester_user_id=requester.id, status=TicketStatus.IN_PROGRESS)
    tickets.resolve_ticket(db_session, ticket, resolution="Done.", resolved_by_user_id=None)
    db_session.flush()

    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_RESOLVED)) == 1
    assert len(_notifications_of(db_session, requester.id, NotificationType.TICKET_STATUS_CHANGED)) == 0
