from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import text

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


def _read_ticket_row(db_session, ticket_id):
    """Reads the ticket directly via raw SQL on the same session/transaction,
    bypassing the ORM identity map.

    `db_session.refresh()` deliberately does not autoflush the object being
    refreshed, and a bare `execute(text(...))` is not reliably guaranteed to
    autoflush an unrelated dirty object either -- both would silently hide
    an uncommitted, un-rolled-back attribute that was staged but never
    flushed, giving a false pass. An explicit `flush()` first reproduces the
    actual risk `db.rollback()` guards against: some later operation on this
    same session (a subsequent query, or another mutation later in a
    longer-lived session) can trigger a flush at any time, and a dirty
    attribute nobody rolled back would ride along with it. Flushing here and
    then reading with raw SQL shows exactly what would be persisted if that
    happened."""
    db_session.flush()
    return db_session.execute(
        text("SELECT priority, assignee_helpdesk_ref FROM tickets WHERE id = :id"),
        {"id": str(ticket_id)},
    ).one()


def test_illegal_status_transition_discards_a_staged_priority_change(client, db_session, make_ticket):
    """The brief's interesting case: priority and status are staged in the
    same call, but the status half is illegal. The whole request must be
    rejected atomically -- no partial persistence of the priority change,
    and no audit row for a mutation that didn't happen."""
    _user, headers = _login(client, db_session, username="patchpriorityrace", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    resp = client.patch(
        f"/api/tickets/{ticket.id}", json={"priority": "urgent", "status": "open"}, headers=headers,
    )

    assert resp.status_code == 409
    row = _read_ticket_row(db_session, ticket.id)
    assert row.priority == "medium"
    assert db_session.query(AuditLog).filter(AuditLog.target_id == str(ticket.id)).count() == 0


def test_patch_cannot_set_status_to_resolved_directly(client, db_session, make_ticket):
    """M3: resolve_ticket() exists to guarantee a non-empty resolution plus
    attribution -- Phase 9's learning loop reads the resolution text. PATCH
    must not be able to reach RESOLVED by a side door: LEGAL_TRANSITIONS
    permits ASSIGNED/IN_PROGRESS/ESCALATED -> RESOLVED (resolve_ticket()
    depends on that transition being legal), but transition_status() itself
    enforces no invariant about resolution data, so PATCH {"status":
    "resolved"} used to reach db.commit() and produce a RESOLVED ticket
    with resolution=None, resolved_at=None, resolved_by_user_id=None."""
    _user, headers = _login(client, db_session, username="patchresolvebypass", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.IN_PROGRESS)

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "resolved"}, headers=headers)

    assert resp.status_code == 400, resp.text
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_PROGRESS
    assert ticket.resolution is None
    assert ticket.resolved_at is None
    assert ticket.resolved_by_user_id is None


def test_reopening_a_resolved_ticket_clears_stale_resolution_fields(client, db_session, make_ticket):
    """M3 (related): resolved -> in_progress is an explicitly legal
    transition (a resolution that didn't hold is a normal helpdesk
    outcome), but leaves the previous resolution/resolved_at/
    resolved_by_user_id sitting on the row -- looking resolved on a ticket
    that is once again actively being worked."""
    user, headers = _login(client, db_session, username="patchreopen", role=Role.HELPDESK, helpdesk_ref="HD-901")
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.RESOLVED)
    ticket.resolution = "Reset the VPN client config."
    ticket.resolved_at = datetime.now(timezone.utc)
    ticket.resolved_by_user_id = user.id
    db_session.commit()

    resp = client.patch(f"/api/tickets/{ticket.id}", json={"status": "in_progress"}, headers=headers)

    assert resp.status_code == 200, resp.text
    db_session.refresh(ticket)
    assert ticket.status == TicketStatus.IN_PROGRESS
    assert ticket.resolution is None
    assert ticket.resolved_at is None
    assert ticket.resolved_by_user_id is None


def test_patch_with_unknown_assignee_ref_is_400(client, db_session, make_ticket):
    """S2: create_ticket's tolerance for an unresolvable assignee ref is
    justified by spec 8.3's exhaustive validation list, but a human-facing
    PATCH silently nulling the FK orphans the ticket -- the original
    assignee loses access and nobody gains it. PATCH should reject an
    unresolvable ref outright."""
    _user, headers = _login(client, db_session, username="patchunknownref", role=Role.ADMIN)
    ticket = make_ticket(assignee_helpdesk_ref="HD-901")

    resp = client.patch(
        f"/api/tickets/{ticket.id}",
        json={"assignee_helpdesk_ref": "HD-DOES-NOT-EXIST", "reassignment_rationale": "Reassigning."},
        headers=headers,
    )

    assert resp.status_code == 400, resp.text
    db_session.refresh(ticket)
    assert ticket.assignee_helpdesk_ref == "HD-901"


def test_illegal_status_transition_discards_a_staged_reassignment(client, db_session, make_ticket):
    """Same property as above, but with a reassignment staged instead of a
    priority change -- the other mutation that can be staged before the
    status transition runs. HD-980 must be a real helpdesk user: S2's
    unresolvable-ref check now runs before the status transition, so an
    unknown ref would itself produce a 400 and never exercise the race
    this test is actually about."""
    _user, headers = _login(client, db_session, username="patchreassignrace", role=Role.HELPDESK, helpdesk_ref="HD-901")
    db_session.add(User(
        username="hd-980-race", email="hd-980-race@northstar.example", full_name="HD-980", password_hash="x",
        role=Role.HELPDESK, helpdesk_ref="HD-980", specialization="Identity and Access Management",
        escalation_authority=EscalationAuthority.HIGH,
    ))
    db_session.commit()
    ticket = make_ticket(assignee_helpdesk_ref="HD-901", status=TicketStatus.CLOSED)

    resp = client.patch(
        f"/api/tickets/{ticket.id}",
        json={
            "assignee_helpdesk_ref": "HD-980",
            "reassignment_rationale": "Needs high escalation authority.",
            "status": "open",
        },
        headers=headers,
    )

    assert resp.status_code == 409
    row = _read_ticket_row(db_session, ticket.id)
    assert row.assignee_helpdesk_ref == "HD-901"
    assert db_session.query(AuditLog).filter(AuditLog.target_id == str(ticket.id)).count() == 0
