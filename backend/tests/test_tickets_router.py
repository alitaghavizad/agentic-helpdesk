from __future__ import annotations

import uuid

from app.db.models import EscalationAuthority, Role, TicketStatus, User


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None) -> dict:
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


def _make_other_user(db_session) -> uuid.UUID:
    """Ticket.requester_user_id is a NOT NULL-checked FK to users.id (when
    set) -- a bare random UUID has no backing row and Postgres raises
    ForeignKeyViolation (app/db/models.py). Same fix as
    tests/test_tickets_scoping.py::_make_user: create a real, unrelated
    User row through db_session so it lands in the same transaction as the
    Ticket insert."""
    other_id = uuid.uuid4()
    db_session.add(User(
        id=other_id, username=f"other-{other_id}", email=f"other-{other_id}@northstar.example",
        full_name="Other User", password_hash="x", role=Role.EMPLOYEE,
    ))
    db_session.commit()
    return other_id


def test_list_tickets_requires_authentication(client):
    assert client.get("/api/tickets").status_code == 401


def test_employee_lists_only_their_own_tickets(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketemp", role=Role.EMPLOYEE)
    mine = make_ticket(requester_user_id=user.id, title="Mine")
    make_ticket(requester_user_id=_make_other_user(db_session), title="Theirs")

    resp = client.get("/api/tickets", headers=headers)

    assert resp.status_code == 200
    titles = {t["title"] for t in resp.json()}
    assert titles == {"Mine"}
    assert resp.json()[0]["ticket_number"] == f"TCK-{mine.ticket_number:06d}"


def test_helpdesk_lists_tickets_assigned_to_them(client, db_session, make_ticket):
    _user, headers = _login(client, db_session, username="tickethd", role=Role.HELPDESK, helpdesk_ref="HD-901")
    make_ticket(assignee_helpdesk_ref="HD-901", title="Assigned to me")
    make_ticket(assignee_helpdesk_ref="HD-902", title="Someone else's")

    resp = client.get("/api/tickets", headers=headers)

    assert {t["title"] for t in resp.json()} == {"Assigned to me"}


def test_list_tickets_filters_by_status(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketfilter", role=Role.EMPLOYEE)
    make_ticket(requester_user_id=user.id, status=TicketStatus.OPEN, title="Open one")
    make_ticket(requester_user_id=user.id, status=TicketStatus.IN_PROGRESS, title="In progress one")

    resp = client.get("/api/tickets?status=in_progress", headers=headers)

    assert {t["title"] for t in resp.json()} == {"In progress one"}


def test_list_tickets_rejects_an_unknown_status(client, db_session):
    _user, headers = _login(client, db_session, username="ticketbadstatus", role=Role.EMPLOYEE)
    assert client.get("/api/tickets?status=nonsense", headers=headers).status_code == 422


def test_get_ticket_returns_the_full_detail(client, db_session, make_ticket):
    user, headers = _login(client, db_session, username="ticketdetail", role=Role.EMPLOYEE)
    ticket = make_ticket(requester_user_id=user.id, title="Detail me")

    resp = client.get(f"/api/tickets/{ticket.id}", headers=headers)

    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "Detail me"
    assert body["body"] == "Body"
    assert body["assignee_helpdesk_ref"] == "HD-901"
    assert body["assignment_rationale"] == "seeded by make_ticket"
    assert body["resolution"] is None


def test_get_ticket_a_principal_does_not_own_is_404_not_403(client, db_session, make_ticket):
    """Spec 6.4: 'there is no code path that returns a ticket the principal
    does not own'. 404 rather than 403 so the endpoint does not confirm the
    existence of tickets the caller may not see."""
    _user, headers = _login(client, db_session, username="ticketnosy", role=Role.EMPLOYEE)
    someone_elses = make_ticket(requester_user_id=_make_other_user(db_session))

    assert client.get(f"/api/tickets/{someone_elses.id}", headers=headers).status_code == 404


def test_get_nonexistent_ticket_is_404(client, db_session):
    _user, headers = _login(client, db_session, username="ticket404", role=Role.EMPLOYEE)
    assert client.get(f"/api/tickets/{uuid.uuid4()}", headers=headers).status_code == 404
