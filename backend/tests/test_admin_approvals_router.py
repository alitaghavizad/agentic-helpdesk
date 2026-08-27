from __future__ import annotations

import uuid
import uuid as _uuid

import pytest

from app.db.models import (
    ApprovalActionType, ApprovalRequest, ApprovalStatus, Conversation, EscalationAuthority,
    RiskLevel, Role, Run, Span, User,
)
from app.db.session import get_sessionmaker


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    """Copied verbatim (module-local, not shared) from
    tests/test_tickets_router.py's `_login`: performs a real login through
    the API rather than fabricating a token, so these tests exercise the
    same auth path production traffic does."""
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


# decide()'s approval path calls tracing.start_run(), which inserts a Run row
# referencing conversation_id through its OWN independently-committing
# connection (see app/tracing/spans.py's module docstring and
# tests/test_approvals_service.py's identical, already-documented fix for
# this exact gap). A Conversation created only through db_session is never
# more than a SAVEPOINT release under this file's db_session fixture, so it
# is invisible to that separate connection's FK check under READ COMMITTED:
# every approve=True test below would fail immediately with
# `IntegrityError ... runs_conversation_id_fkey` the instant decide() calls
# start_run(). `pending` below creates the Conversation through a real,
# hard-committing session instead, and registers its id here for
# `_sweep_hard_committed_rows_after_module` to delete once every test in
# this module has released its db_session lock on it.
_hard_committed_conversation_ids: list[_uuid.UUID] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """Deletes the Conversation/Run/Span rows `pending` hard-commits, but
    only after every test in this module has finished. Each test's own
    db_session holds a FOR KEY SHARE lock on its Conversation row -- taken
    when ApprovalRequest was inserted referencing it -- for as long as that
    test's transaction stays open, so deleting it any earlier would hang
    waiting on a lock that only releases at that test's own teardown.
    Mirrors test_approvals_service.py's identically-motivated module-scoped
    final sweep."""
    yield
    Session = get_sessionmaker()
    with Session() as session:
        if _hard_committed_conversation_ids:
            run_ids = [
                r for (r,) in session.query(Run.id).filter(
                    Run.conversation_id.in_(_hard_committed_conversation_ids)
                )
            ]
            if run_ids:
                session.query(Span).filter(Span.run_id.in_(run_ids)).delete(synchronize_session=False)
                session.query(Run).filter(Run.id.in_(run_ids)).delete(synchronize_session=False)
            session.query(Conversation).filter(
                Conversation.id.in_(_hard_committed_conversation_ids)
            ).delete(synchronize_session=False)
        session.commit()


@pytest.fixture()
def pending(db_session):
    Session = get_sessionmaker()
    with Session() as hard_session:
        conv = Conversation(guest_name="G", guest_email="g@northstar.example")
        hard_session.add(conv)
        hard_session.commit()
        conversation_id = conv.id
    _hard_committed_conversation_ids.append(conversation_id)

    row = ApprovalRequest(
        conversation_id=conversation_id, task_id=None, requester_user_id=None,
        action_type=ApprovalActionType.RESET_CREDENTIAL,
        action_payload={"target_username": "someone", "credential_kind": "password"},
        justification="j", risk_level=RiskLevel.HIGH, agent_summary="a",
        status=ApprovalStatus.PENDING,
    )
    db_session.add(row)
    db_session.commit()
    return row


def test_listing_requires_admin(client, db_session):
    _, employee_headers = _login(client, db_session, username="approvals-emp", role=Role.EMPLOYEE)
    _, helpdesk_headers = _login(
        client, db_session, username="approvals-hd", role=Role.HELPDESK, helpdesk_ref="HD-901",
    )
    _, admin_headers = _login(client, db_session, username="approvals-admin1", role=Role.ADMIN)

    assert client.get("/api/admin/approvals", headers=employee_headers).status_code == 403
    assert client.get("/api/admin/approvals", headers=helpdesk_headers).status_code == 403
    assert client.get("/api/admin/approvals", headers=admin_headers).status_code == 200


def test_listing_returns_the_agent_justification_and_full_payload(client, db_session, pending):
    """Spec 15's Approvals screen shows the justification, risk level, and
    full payload -- the admin cannot judge a request they cannot see."""
    _, admin_headers = _login(client, db_session, username="approvals-admin2", role=Role.ADMIN)

    body = client.get("/api/admin/approvals?status=pending", headers=admin_headers).json()
    entry = next(e for e in body if e["id"] == str(pending.id))
    assert entry["justification"] == "j"
    assert entry["risk_level"] == "high"
    assert entry["action_payload"] == {"target_username": "someone", "credential_kind": "password"}
    assert entry["request_number"] == f"REQ-{pending.request_number:06d}"


def test_deciding_requires_admin(client, db_session, pending):
    _, employee_headers = _login(client, db_session, username="approvals-emp2", role=Role.EMPLOYEE)

    response = client.post(
        f"/api/admin/approvals/{pending.id}/decide",
        json={"approve": True, "note": ""},
        headers=employee_headers,
    )
    assert response.status_code == 403


def test_approving_returns_the_terminal_status_in_one_round_trip(client, db_session, pending):
    _, admin_headers = _login(client, db_session, username="approvals-admin3", role=Role.ADMIN)

    response = client.post(
        f"/api/admin/approvals/{pending.id}/decide",
        json={"approve": True, "note": "Looks fine"},
        headers=admin_headers,
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "executed"
    assert body["execution_result"]["simulated"] is True


def test_deciding_twice_is_a_conflict(client, db_session, pending):
    _, admin_headers = _login(client, db_session, username="approvals-admin4", role=Role.ADMIN)

    client.post(f"/api/admin/approvals/{pending.id}/decide", json={"approve": True, "note": ""}, headers=admin_headers)
    second = client.post(
        f"/api/admin/approvals/{pending.id}/decide", json={"approve": True, "note": ""}, headers=admin_headers,
    )
    assert second.status_code == 409


def test_deciding_an_unknown_request_is_404(client, db_session):
    _, admin_headers = _login(client, db_session, username="approvals-admin5", role=Role.ADMIN)

    response = client.post(
        f"/api/admin/approvals/{uuid.uuid4()}/decide",
        json={"approve": True, "note": ""},
        headers=admin_headers,
    )
    assert response.status_code == 404
