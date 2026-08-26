from __future__ import annotations

import pytest

from app.db.models import EscalationAuthority, Role, Ticket, TicketStatus, User
from app.tickets.routing import OPEN_STATUSES, open_workload, workload_by_specialist


def _make_helpdesk_user(db_session, ref: str, specialization: str, escalation: EscalationAuthority = EscalationAuthority.STANDARD) -> User:
    user = User(
        username=ref.lower(), email=f"{ref.lower()}@northstar.example", full_name=ref, password_hash="x",
        role=Role.HELPDESK, helpdesk_ref=ref, specialization=specialization, escalation_authority=escalation,
    )
    db_session.add(user)
    db_session.commit()
    return user


def test_open_statuses_excludes_terminal_states():
    assert OPEN_STATUSES == (TicketStatus.OPEN, TicketStatus.ASSIGNED, TicketStatus.IN_PROGRESS)
    assert TicketStatus.RESOLVED not in OPEN_STATUSES
    assert TicketStatus.CLOSED not in OPEN_STATUSES


def test_open_workload_counts_only_open_statuses_for_one_ref(db_session, make_ticket):
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)
    make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert open_workload(db_session, "HD-901") == 2


def test_workload_by_specialist_groups_counts_per_ref(db_session, make_ticket):
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.ASSIGNED)
    make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)
    make_ticket(assignee_helpdesk_ref="HD-903", status=TicketStatus.RESOLVED)

    counts = workload_by_specialist(db_session)

    assert counts["HD-901"] == 2
    assert counts["HD-902"] == 1
    assert "HD-903" not in counts
