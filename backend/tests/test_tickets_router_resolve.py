from __future__ import annotations

from app.db.models import AuditLog, EscalationAuthority, Role, TicketStatus, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
        specialization="Network and VPN Support" if helpdesk_ref else None,
        escalation_authority=EscalationAuthority.STANDARD if helpdesk_ref else None,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_helpdesk_resolves_their_own_ticket(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolvehd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    resp = client.post(
        f"/api/tickets/{ticket.id}/resolve",
        json={"resolution": "Reissued the VPN certificate."}, headers=headers,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "resolved"
    assert body["resolution"] == "Reissued the VPN certificate."
    assert body["resolved_at"] is not None

    db_session.refresh(ticket)
    assert ticket.resolved_by_user_id == user.id


def test_employee_cannot_resolve(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolveemp", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id, status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "I fixed it myself"}, headers=headers,
    ).status_code == 403


def test_helpdesk_cannot_resolve_someone_elses_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolvehdother", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "not mine"}, headers=headers,
    ).status_code == 404


def test_resolving_a_closed_ticket_is_409(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolveclosed", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "too late"}, headers=headers,
    ).status_code == 409


def test_blank_resolution_is_422(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="resolveblank", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    assert client.post(
        f"/api/tickets/{ticket.id}/resolve", json={"resolution": "   "}, headers=headers,
    ).status_code == 422


def test_resolve_writes_an_audit_row(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="resolveaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    client.post(f"/api/tickets/{ticket.id}/resolve", json={"resolution": "Done."}, headers=headers)

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "ticket.resolve", AuditLog.target_id == str(ticket.id),
    ).one()
    assert row.actor_id == str(user.id)
    assert row.payload["resolution"] == "Done."
