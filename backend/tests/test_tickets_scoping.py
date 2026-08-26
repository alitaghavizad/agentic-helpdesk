from __future__ import annotations

import uuid

from app.db.models import Role, Ticket, TicketStatus, User
from app.rbac.policy import Principal
from app.tickets.scoping import can_read_ticket, scope_tickets_query

_REQUESTER_ID = uuid.uuid4()
_OTHER_ID = uuid.uuid4()


def _make_user(db_session, user_id: uuid.UUID) -> None:
    """Ticket.requester_user_id is a NOT NULL-checked FK to users.id (when
    set) -- a row with that id must exist in the same db_session transaction
    before a Ticket can reference it, or Postgres raises ForeignKeyViolation.
    Created through db_session (not get_sessionmaker()) so it lands in the
    same transaction/connection as the Ticket insert and is visible to it."""
    db_session.add(User(
        id=user_id, username=f"user-{user_id}", email=f"{user_id}@example.com",
        full_name="Test User", password_hash="x", role=Role.EMPLOYEE,
    ))
    db_session.commit()

_EMPLOYEE = Principal(kind="user", user_id=str(_REQUESTER_ID), role="employee", clearance="standard", department="Engineering", employee_ref="EMP-001", helpdesk_ref=None)
_OTHER_EMPLOYEE = Principal(kind="user", user_id=str(_OTHER_ID), role="employee", clearance="standard", department="Engineering", employee_ref="EMP-002", helpdesk_ref=None)
_HELPDESK = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref="HD-901")
_HELPDESK_OTHER = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref="HD-902")
_ADMIN = Principal(kind="user", user_id=str(uuid.uuid4()), role="admin", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)
_GUEST = Principal(kind="guest", user_id=None, role="guest", clearance=None, department=None, employee_ref=None, helpdesk_ref=None, guest_name="G", guest_email="guest@example.com")


def _scoped_ids(db_session, principal, guest_email=None):
    query = scope_tickets_query(db_session.query(Ticket), principal, guest_email=guest_email)
    return {t.id for t in query.all()}


def test_employee_sees_only_tickets_they_requested(db_session, make_ticket):
    _make_user(db_session, _REQUESTER_ID)
    _make_user(db_session, _OTHER_ID)
    mine = make_ticket(requester_user_id=_REQUESTER_ID, requester_guest_email=None)
    theirs = make_ticket(requester_user_id=_OTHER_ID, requester_guest_email=None)

    ids = _scoped_ids(db_session, _EMPLOYEE)

    assert mine.id in ids
    assert theirs.id not in ids


def test_helpdesk_sees_tickets_assigned_to_them_not_ones_they_requested(db_session, make_ticket):
    assigned = make_ticket(assignee_helpdesk_ref="HD-901")
    other = make_ticket(assignee_helpdesk_ref="HD-902")

    ids = _scoped_ids(db_session, _HELPDESK)

    assert assigned.id in ids
    assert other.id not in ids


def test_admin_is_unrestricted(db_session, make_ticket):
    _make_user(db_session, _REQUESTER_ID)
    _make_user(db_session, _OTHER_ID)
    a = make_ticket(assignee_helpdesk_ref="HD-901", requester_user_id=_REQUESTER_ID)
    b = make_ticket(assignee_helpdesk_ref="HD-902", requester_user_id=_OTHER_ID)

    ids = _scoped_ids(db_session, _ADMIN)

    assert {a.id, b.id} <= ids


def test_guest_sees_only_their_own_email(db_session, make_ticket):
    mine = make_ticket(requester_guest_email="guest@example.com")
    theirs = make_ticket(requester_guest_email="someone-else@example.com")

    ids = _scoped_ids(db_session, _GUEST, guest_email="guest@example.com")

    assert mine.id in ids
    assert theirs.id not in ids


def test_helpdesk_with_no_ref_sees_nothing_rather_than_everything(db_session, make_ticket):
    """Fail closed: a helpdesk principal whose JWT carries no helpdesk_ref
    must match zero rows, never fall through to an unfiltered query."""
    make_ticket(assignee_helpdesk_ref="HD-901")
    broken = Principal(kind="user", user_id=str(uuid.uuid4()), role="helpdesk", clearance=None, department=None, employee_ref=None, helpdesk_ref=None)

    assert _scoped_ids(db_session, broken) == set()


def test_can_read_ticket_agrees_with_the_query_filter(db_session, make_ticket):
    _make_user(db_session, _REQUESTER_ID)
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", requester_user_id=_REQUESTER_ID)

    assert can_read_ticket(_EMPLOYEE, ticket) is True
    assert can_read_ticket(_OTHER_EMPLOYEE, ticket) is False
    assert can_read_ticket(_HELPDESK, ticket) is True
    assert can_read_ticket(_HELPDESK_OTHER, ticket) is False
    assert can_read_ticket(_ADMIN, ticket) is True
    assert can_read_ticket(_GUEST, ticket, guest_email="guest@example.com") is False
