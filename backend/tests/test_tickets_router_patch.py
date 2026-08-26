from __future__ import annotations

import uuid

from app.db.models import ActorType, AuditLog, EscalationAuthority, Role, TicketStatus, User


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


def test_employee_cannot_patch_a_ticket_even_their_own(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="patchemp", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    assert resp.status_code == 403


def test_helpdesk_advances_status_on_their_own_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchhd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    assert resp.status_code == 200
    assert resp.json()["status"] == "in_progress"
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_PROGRESS


def test_helpdesk_cannot_patch_a_ticket_assigned_to_someone_else(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchhdother", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers).status_code == 404


def test_admin_can_patch_any_ticket(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchadmin", role=Role.ADMIN)
    ticket = make_ticket(assignee_helpdesk_ref="HD-902", status=TicketStatus.OPEN)

    assert client.patch(f"/api/tickets/{ticket.id}", json={"priority": "urgent"}, headers=headers).status_code == 200
    db_session.refresh(ticket)
    assert ticket.priority.value == "urgent"


def test_illegal_status_transition_is_409(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchillegal", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "open"}, headers=headers)

    assert resp.status_code == 409
    assert "closed" in resp.json()["detail"]


def test_patch_with_no_fields_is_400(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchempty", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    assert client.patch(f"/api/tickets/{ticket.id}", json={}, headers=headers).status_code == 400


def test_reassignment_updates_ref_and_rationale(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchreassign", role=Role.ADMIN)
    db_session.add(User(
        username="hd-980", email="hd-980@northstar.example", full_name="HD-980", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-980", specialization="Identity and Access Management",
        escalation_authority=EscalationAuthority.HIGH,
    ))
    db_session.commit()
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    resp = client.patch(
        f"/api/tickets/{ticket.id}",
        json={"assignee_helpdesk_ref": "HD-980", "reassignment_rationale": "Needs high escalation authority."},
        headers=headers,
    )

    assert resp.status_code == 200
    assert resp.json()["assignee_helpdesk_ref"] == "HD-980"
    assert "Needs high escalation authority." in resp.json()["assignment_rationale"]


def test_reassignment_without_a_rationale_is_400(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="patchnorationale", role=Role.ADMIN)
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    assert client.patch(
        f"/api/tickets/{ticket.id}", json={"assignee_helpdesk_ref": "HD-980"}, headers=headers,
    ).status_code == 400


def test_every_successful_patch_writes_one_audit_row(client, db_session, make_ticket):
    """Spec 14: 'a role check plus an audit-log write on every mutating call'."""
    user, headers = _login(client, db_session, username="patchaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.OPEN)

    client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    row = db_session.query(AuditLog).filter(
        AuditLog.action == "ticket.update", AuditLog.target_id == str(ticket.id),
    ).one()
    assert row.actor_type == ActorType.USER
    assert row.actor_id == str(user.id)
    assert row.target_type == "ticket"
    assert row.payload["changes"]["status"] == {"from": "open", "to": "in_progress"}


def test_a_rejected_patch_writes_no_audit_row(client, db_session, make_ticket):
    """record_audit deliberately does not commit, so a failed mutation must
    leave no audit trace behind."""
    _user, headers = _login(client, db_session, username="patchnoaudit", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    client.patch(f"/api/tickets/{ticket.id}", json={"status": "open"}, headers=headers)

    assert db_session.query(AuditLog).filter(AuditLog.target_id == str(ticket.id)).count() == 0
