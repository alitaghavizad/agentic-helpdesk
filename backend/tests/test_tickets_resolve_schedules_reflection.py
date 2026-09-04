"""Confirms POST /tickets/{id}/resolve schedules a reflection background
task with the resolved ticket's id. Does NOT assert on the task actually
running -- BackgroundTasks execution timing under TestClient is an
implementation detail of Starlette, not this endpoint's contract. The
contract is: something got scheduled, for the right ticket, unconditionally
on a successful resolve.
"""
from __future__ import annotations

import uuid

from app.auth.security import hash_password
from app.db.models import EscalationAuthority, Role, TicketStatus, User


def _login_helpdesk(client, db_session, *, helpdesk_ref="HD-901"):
    user = User(
        username="resolver", email="resolver@northstar.example", full_name="Resolver",
        password_hash=hash_password("Passw0rd!dev"), role=Role.HELPDESK, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support", escalation_authority=EscalationAuthority.STANDARD,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": "resolver", "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_resolve_schedules_reflection_for_the_resolved_ticket(client, db_session, make_ticket, monkeypatch):
    import app.tickets.router as tickets_router_module

    scheduled: list[uuid.UUID] = []

    def _fake_reflect(ticket_id):
        scheduled.append(ticket_id)

    monkeypatch.setattr(tickets_router_module, "reflect", _fake_reflect)

    headers = _login_helpdesk(client, db_session)
    # make_ticket() defaults to status=TicketStatus.OPEN, but resolve_ticket()
    # only allows ASSIGNED/IN_PROGRESS/ESCALATED -> RESOLVED (see
    # test_tickets_router_resolve.py, which passes this explicitly on every
    # call for the same reason). Without it, resolve returns 409 before this
    # test ever reaches the scheduling behavior under test.
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    resp = client.post(f"/api/tickets/{ticket.id}/resolve", json={"resolution": "Fixed it."}, headers=headers)

    assert resp.status_code == 200, resp.text
    assert scheduled == [ticket.id]
