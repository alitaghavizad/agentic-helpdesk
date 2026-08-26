from __future__ import annotations

import uuid

import pytest

from app.db.models import TicketStatus
from app.tickets.service import (
    LEGAL_TRANSITIONS, InvalidTransition, reassign, resolve_ticket, transition_status,
)


def test_closed_is_terminal():
    assert LEGAL_TRANSITIONS[TicketStatus.CLOSED] == frozenset()


def test_every_status_has_an_entry_so_a_new_enum_value_cannot_be_forgotten():
    assert set(LEGAL_TRANSITIONS) == set(TicketStatus)


@pytest.mark.parametrize("start,target", [
    (TicketStatus.OPEN, TicketStatus.ASSIGNED),
    (TicketStatus.OPEN, TicketStatus.IN_PROGRESS),
    (TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS),
    (TicketStatus.IN_PROGRESS, TicketStatus.RESOLVED),
    (TicketStatus.IN_PROGRESS, TicketStatus.ESCALATED),
    (TicketStatus.ESCALATED, TicketStatus.RESOLVED),
    (TicketStatus.RESOLVED, TicketStatus.CLOSED),
    (TicketStatus.RESOLVED, TicketStatus.IN_PROGRESS),
])
def test_legal_transitions_are_applied(db_session, make_ticket, start, target):
    ticket = make_ticket(status=start)
    transition_status(db_session, ticket, target)
    db_session.commit()
    assert ticket.status == target


@pytest.mark.parametrize("start,target", [
    (TicketStatus.CLOSED, TicketStatus.OPEN),
    (TicketStatus.CLOSED, TicketStatus.IN_PROGRESS),
    (TicketStatus.OPEN, TicketStatus.RESOLVED),
    (TicketStatus.RESOLVED, TicketStatus.ASSIGNED),
])
def test_illegal_transitions_raise_and_leave_the_row_untouched(db_session, make_ticket, start, target):
    ticket = make_ticket(status=start)

    with pytest.raises(InvalidTransition):
        transition_status(db_session, ticket, target)

    assert ticket.status == start


def test_transition_status_accepts_a_raw_string(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.OPEN)
    transition_status(db_session, ticket, "in_progress")
    db_session.commit()
    assert ticket.status == TicketStatus.IN_PROGRESS


def test_transition_status_rejects_an_unknown_status_string(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.OPEN)
    with pytest.raises(ValueError):
        transition_status(db_session, ticket, "not-a-status")


def test_resolve_ticket_sets_resolution_fields_and_status(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)

    resolve_ticket(db_session, ticket, resolution="Reset the VPN profile.", resolved_by_user_id=None)
    db_session.commit()

    assert ticket.status == TicketStatus.RESOLVED
    assert ticket.resolution == "Reset the VPN profile."
    assert ticket.resolved_at is not None


def test_resolve_ticket_records_who_resolved_it(db_session, make_ticket):
    from app.db.models import EscalationAuthority, Role, User

    resolver = User(
        username="hd-960", email="hd-960@northstar.example", full_name="HD-960", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-960", specialization="Network and VPN Support",
        escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(resolver)
    db_session.commit()

    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)
    resolve_ticket(db_session, ticket, resolution="Done.", resolved_by_user_id=resolver.id)
    db_session.commit()

    assert ticket.resolved_by_user_id == resolver.id


def test_resolve_ticket_refuses_an_illegal_source_status(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.CLOSED)
    with pytest.raises(InvalidTransition):
        resolve_ticket(db_session, ticket, resolution="too late", resolved_by_user_id=None)


def test_resolve_ticket_requires_a_non_empty_resolution(db_session, make_ticket):
    ticket = make_ticket(status=TicketStatus.IN_PROGRESS)
    with pytest.raises(ValueError):
        resolve_ticket(db_session, ticket, resolution="   ", resolved_by_user_id=None)


def test_reassign_updates_ref_user_id_and_rationale(db_session, make_ticket):
    from app.db.models import EscalationAuthority, Role, User

    new_owner = User(
        username="hd-970", email="hd-970@northstar.example", full_name="HD-970", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-970", specialization="Identity and Access Management",
        escalation_authority=EscalationAuthority.HIGH,
    )
    db_session.add(new_owner)
    db_session.commit()

    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    reassign(db_session, ticket, assignee_helpdesk_ref="HD-970", rationale="Escalation authority required.")
    db_session.commit()

    assert ticket.assignee_helpdesk_ref == "HD-970"
    assert ticket.assignee_user_id == new_owner.id
    assert "Escalation authority required." in ticket.assignment_rationale


def test_reassign_to_an_unknown_ref_nulls_the_fk_but_keeps_the_ref(db_session, make_ticket):
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    reassign(db_session, ticket, assignee_helpdesk_ref="HD-NOPE", rationale="manual override")
    db_session.commit()

    assert ticket.assignee_helpdesk_ref == "HD-NOPE"
    assert ticket.assignee_user_id is None
