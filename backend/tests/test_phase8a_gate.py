"""Phase 8a gate: every admin endpoint returns correct data, enforces
admin-only access, and audits every mutation (spec section 5's gate line).

WHAT THIS DOES NOT PROVE: that a real model can fill the IncidentDossier
schema. Nothing in the default suite proves that -- the client is stubbed
everywhere. It is proven only by tests/test_admin_dossier_live.py, and the
phase report must cite that live run rather than this file.
"""
from __future__ import annotations

import uuid

import pytest

from app.db.models import (
    ActorType, Lesson, LessonConfidence, LessonStatus, Role, Run, RunStatus,
    RunTrigger, User,
)

# Every read endpoint on the admin surface. The trace route is
# parameterised by a run id, but authorization is checked before the id is
# looked up, so a random uuid exercises the same gate.
READ_PATHS = [
    "/api/admin/overview",
    "/api/admin/runs",
    f"/api/admin/runs/{uuid.uuid4()}/trace",
    "/api/admin/conversations",
    "/api/admin/audit",
    "/api/admin/costs",
    "/api/admin/users",
    "/api/admin/lessons",
    "/api/admin/approvals",
]

# The paginated ones. /overview, /costs and /trace return objects, not
# pages, and /approvals is the phase 6 route, which is not paginated.
PAGED_PATHS = [
    "/api/admin/runs",
    "/api/admin/conversations",
    "/api/admin/audit",
    "/api/admin/users",
    "/api/admin/lessons",
]


def _login(client, db_session, *, username: str, role: Role):
    """Copied from tests/test_admin_read_endpoints.py -- there is no shared
    auth fixture in this project. `full_name` is NOT NULL with no default."""
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _admin(client, db_session):
    return _login(client, db_session, username=f"g8{uuid.uuid4().hex[:12]}", role=Role.ADMIN)


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_admin_read_endpoint_answers_for_an_admin(client, db_session, path):
    _user, headers = _admin(client, db_session)
    response = client.get(path, headers=headers)
    # The trace route legitimately 404s for a run that does not exist; what
    # the gate asserts is that it ANSWERS rather than erroring or 403ing.
    assert response.status_code in (200, 404), response.text


@pytest.mark.parametrize("path", READ_PATHS)
@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.HELPDESK])
def test_every_admin_read_endpoint_rejects_non_admins(client, db_session, path, role):
    _user, headers = _login(
        client, db_session, username=f"g8{uuid.uuid4().hex[:12]}", role=role,
    )
    assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_admin_read_endpoint_rejects_a_guest(client, path):
    resp = client.post("/api/auth/guest", json={"name": "V", "email": "v@example.com"})
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}
    assert client.get(path, headers=headers).status_code == 403


@pytest.mark.parametrize("path", READ_PATHS)
def test_every_admin_read_endpoint_rejects_an_anonymous_caller(client, path):
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", PAGED_PATHS)
def test_every_list_endpoint_caps_its_limit(client, db_session, path):
    """Clamped, not rejected: an over-large limit is a client bug, not an
    attack, and 422ing it helps nobody. The cap is what stops a single
    request walking 20,000 spans or 500 runs."""
    _user, headers = _admin(client, db_session)
    body = client.get(f"{path}?limit=99999", headers=headers).json()
    assert body["limit"] == 200
    assert len(body["items"]) <= 200


@pytest.mark.parametrize("path", PAGED_PATHS)
def test_every_list_endpoint_returns_the_full_envelope(client, db_session, path):
    """Phase 8b renders a pager from these four keys. A list endpoint that
    returned a bare array would leave it unable to."""
    _user, headers = _admin(client, db_session)
    body = client.get(path, headers=headers).json()
    assert set(body) >= {"items", "total", "limit", "offset"}
    assert body["limit"] == 50 and body["offset"] == 0
    assert isinstance(body["total"], int)


def test_every_mutation_writes_an_audit_row(client, db_session):
    """The gate's third clause. Both mutating endpoints are exercised, and
    the audit rows are counted before and after so a pre-existing row
    cannot stand in for a new one."""
    from app.db.models import AuditLog

    admin, headers = _admin(client, db_session)
    subject, _ = _login(
        client, db_session, username=f"g8s{uuid.uuid4().hex[:12]}", role=Role.EMPLOYEE,
    )
    # Lesson.created_by_run_id is NOT NULL, so a Run has to exist first --
    # built through db_session, never tracing.start_run(), which commits on
    # its own connection.
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()
    lesson = Lesson(
        title="Gate lesson", category="vpn_network", content_md="body",
        file_path="lessons/gate.md", applies_to=["vpn"],
        confidence=LessonConfidence.MEDIUM, status=LessonStatus.ACTIVE,
        created_by_run_id=run.id,
    )
    db_session.add(lesson)
    db_session.commit()

    before = db_session.query(AuditLog).count()

    assert client.patch(
        f"/api/admin/users/{subject.id}", headers=headers, json={"role": "helpdesk"},
    ).status_code == 200
    assert client.patch(
        f"/api/admin/lessons/{lesson.id}", headers=headers, json={"title": "Gate lesson, corrected"},
    ).status_code == 200
    assert client.delete(
        f"/api/admin/lessons/{lesson.id}", headers=headers,
    ).status_code == 200

    rows = db_session.query(AuditLog).order_by(AuditLog.created_at.asc()).all()
    assert len(rows) - before == 3, "one audit row per mutation"
    actions = [r.action for r in rows[before:]]
    assert actions == ["user.updated", "lesson.updated", "lesson.archived"]
    assert all(r.actor_type == ActorType.USER for r in rows[before:])
    assert all(str(r.actor_id) == str(admin.id) for r in rows[before:])
