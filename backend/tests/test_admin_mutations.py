"""The mutating admin endpoints (spec 14, 4.2): users and lessons.

Three things these tests are deliberately strict about.

**Authorization is proven per endpoint and per principal.** A router prefix
is not a permission. Every one of the five routes added here is asserted
against employee, helpdesk, guest AND anonymous, because a single route that
forgets `AdminPrincipal` hands the whole user table -- and a role-editing
verb -- to anyone with a token.

**Field smuggling is proven, not assumed.** `PATCH /users/{id}` relies on
Pydantic ignoring unknown fields. That is a default, and a default that is
never exercised is a default that can be changed by accident. The smuggle
test sends `username`, `password_hash`, `is_active`, `email`, `id` and
`role` together and asserts every field except `role` is byte-identical
afterwards.

**The single-transaction guarantee is proven against a real, committing
session.** Spec 8 requires that `PATCH /users/{id}` write the change and its
audit row in one transaction and that neither survive a rollback. That
cannot be shown through the ordinary `client` fixture: `tests/conftest.py`
binds `db_session` to an outer connection-level transaction with
`join_transaction_mode="create_savepoint"`, so a `db.commit()` inside a
handler only RELEASEs a savepoint -- nothing ever reaches another connection
whether the handler is correct or not, and an assertion made from a second
connection would pass vacuously. `_committing_client` below therefore wires
the app to a genuinely independent session so a split transaction would be
visible to a second connection, which is the only way the assertion can
fail for the right reason.

That real session is also the only reason this module hard-commits anything,
and hard-committed rows are NOT rolled back at teardown -- a leaked `users`
row breaks `tests/test_seed.py`'s exact count of 126 for everyone. Hence the
module-scoped sweep, following `tests/test_approvals_service.py`.
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.db.models import (
    ActorType, AuditLog, Clearance, Lesson, LessonConfidence, LessonStatus,
    RefreshToken, Role, Run, RunStatus, RunTrigger, User,
)
from app.db.session import get_sessionmaker


def _login(client, db_session, *, username: str, role: Role, helpdesk_ref: str | None = None):
    """Copied from tests/test_tickets_router.py -- there is no shared auth
    fixture in this project. `full_name` is NOT NULL with no default, so it
    must be set explicitly on every User built in a test."""
    from app.auth.security import hash_password

    user = User(
        username=username, email=f"{username}@northstar.example", full_name=username.title(),
        password_hash=hash_password("Passw0rd!dev"), role=role, helpdesk_ref=helpdesk_ref,
    )
    db_session.add(user)
    db_session.commit()
    resp = client.post("/api/auth/login", json={"username": username, "password": "Passw0rd!dev"})
    assert resp.status_code == 200, resp.text
    return user, {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _guest_login(client) -> dict:
    """Copied from tests/test_notifications_router.py. A guest principal has
    kind='guest' and role='guest', so it reaches require_role and is refused
    with 403 -- not 401, which is reserved for having no token at all."""
    resp = client.post("/api/auth/guest", json={"name": "Visitor", "email": "visitor@example.com"})
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def _make_user(db_session, **overrides) -> User:
    """A target row for the PATCH tests, through db_session so it rolls back."""
    suffix = uuid.uuid4().hex[:8]
    fields = dict(
        username=f"tgt{suffix}", email=f"tgt{suffix}@northstar.example",
        full_name="Target User", password_hash="original-hash", role=Role.EMPLOYEE,
        clearance=Clearance.STANDARD, is_active=True,
    )
    fields.update(overrides)
    user = User(**fields)
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def _make_lesson(db_session, **overrides) -> Lesson:
    """`Lesson.created_by_run_id` is a NOT NULL FK to `runs.id` (app/db/models.py)
    -- the plan's draft passed None, which cannot insert. A Run created through
    the same db_session lands in the same transaction, so the FK resolves and
    both roll back together."""
    run = Run(trigger=RunTrigger.CHAT_TURN, status=RunStatus.OK)
    db_session.add(run)
    db_session.commit()

    fields = dict(
        ticket_id=None, title="A lesson", category="vpn_network", content_md="body",
        file_path="knowledge/lessons/a.md", applies_to=[],
        confidence=LessonConfidence.LOW, status=LessonStatus.ACTIVE,
        created_by_run_id=run.id,
    )
    fields.update(overrides)
    lesson = Lesson(**fields)
    db_session.add(lesson)
    db_session.commit()
    db_session.refresh(lesson)
    return lesson


# ---- Hard-committed rows and their sweep ------------------------------------------

# Only the single-transaction test below hard-commits, and it must: the whole
# point is that a second connection can see what the first one committed. A
# row created through db_session is never more than a SAVEPOINT release and is
# invisible to any other connection, so the assertion would hold whether or
# not the handler is correct. See this module's docstring.
_hard_committed_user_ids: list[uuid.UUID] = []


@pytest.fixture(scope="module", autouse=True)
def _sweep_hard_committed_rows_after_module():
    """Deletes every hard-committed row this module creates, but only once
    every test in the module has finished and released its locks -- deleting
    earlier would block on a row lock held by a still-open test transaction.
    Mirrors tests/test_approvals_service.py's identically-motivated sweep.

    Order matters: `refresh_tokens` has a FK to `users.id` and `POST
    /api/auth/login` commits one, so the users DELETE fails with a
    ForeignKeyViolation unless the tokens go first. `audit_log` has no FK but
    is swept too, so a rerun of this module cannot slowly inflate the table
    whose whole purpose is being read in order."""
    yield
    if not _hard_committed_user_ids:
        return
    Session = get_sessionmaker()
    with Session() as session:
        ids = list(_hard_committed_user_ids)
        session.query(RefreshToken).filter(
            RefreshToken.user_id.in_(ids),
        ).delete(synchronize_session=False)
        session.query(AuditLog).filter(
            AuditLog.target_id.in_([str(i) for i in ids]),
        ).delete(synchronize_session=False)
        session.query(User).filter(User.id.in_(ids)).delete(synchronize_session=False)
        session.commit()


def _hard_commit_user(**overrides) -> uuid.UUID:
    """Inserts a User on its own connection and commits, so every other
    connection can see it. Registers the id for the module sweep."""
    from app.auth.security import hash_password

    suffix = uuid.uuid4().hex[:8]
    fields = dict(
        username=f"mtx{suffix}", email=f"mtx{suffix}@northstar.example",
        full_name="Transaction Target", password_hash=hash_password("Passw0rd!dev"),
        role=Role.EMPLOYEE, clearance=Clearance.STANDARD, is_active=True,
    )
    fields.update(overrides)
    Session = get_sessionmaker()
    with Session() as session:
        user = User(**fields)
        session.add(user)
        session.commit()
        _hard_committed_user_ids.append(user.id)
        return user.id


@pytest.fixture()
def committing_client():
    """A TestClient whose request session is a REAL, independently committing
    session instead of the outer-transaction-bound `db_session`.

    `tests/conftest.py`'s `client` fixture hands the handler the same
    savepoint-joined session every test rolls back, which makes a handler's
    `db.commit()` a no-op as far as any other connection is concerned. That is
    exactly the property the single-transaction test needs to NOT hold: it has
    to be able to observe a handler that committed half its work."""
    from app.db.session import get_db as _get_db
    from app.main import app

    Session = get_sessionmaker()
    session = Session()

    def _override_get_db():
        yield session

    app.dependency_overrides[_get_db] = _override_get_db
    try:
        with TestClient(app) as test_client:
            yield test_client, session
    finally:
        # Rolls back whatever the failing request left staged, and releases
        # the row locks the module sweep will need.
        session.rollback()
        session.close()
        app.dependency_overrides.pop(_get_db, None)


# ---- PATCH /users/{id} ------------------------------------------------------------

def test_patching_a_user_changes_role_and_clearance_and_audits_it(client, db_session):
    target = _make_user(db_session)

    _admin, headers = _login(client, db_session, username="muadm", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{target.id}",
        json={"role": "helpdesk", "clearance": "sensitive"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "id": str(target.id), "role": "helpdesk", "clearance": "sensitive",
    }
    db_session.refresh(target)
    assert target.role is Role.HELPDESK
    assert target.clearance is Clearance.SENSITIVE

    rows = db_session.query(AuditLog).filter(
        AuditLog.target_type == "user", AuditLog.target_id == str(target.id),
    ).all()
    assert len(rows) == 1
    assert rows[0].action == "user.updated"
    assert rows[0].actor_type is ActorType.USER
    assert rows[0].actor_id == str(_admin.id)
    assert rows[0].payload == {
        "previous_role": "employee", "previous_clearance": "standard",
        "new_role": "helpdesk", "new_clearance": "sensitive",
    }


def test_patching_a_user_with_only_a_role_leaves_clearance_alone(client, db_session):
    """An omitted field is "leave it", not "set it to null". A PATCH that
    silently cleared clearance would quietly demote a privileged account."""
    target = _make_user(db_session, clearance=Clearance.PRIVILEGED)

    _admin, headers = _login(client, db_session, username="mupart", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{target.id}", json={"role": "helpdesk"}, headers=headers,
    )

    assert response.status_code == 200, response.text
    db_session.refresh(target)
    assert target.role is Role.HELPDESK
    assert target.clearance is Clearance.PRIVILEGED


def test_patching_a_user_cannot_change_anything_else(client, db_session):
    """The endpoint edits role and clearance. An admin who can rewrite a
    username or a password hash through this route has a privilege escalation
    path that no audit row would explain -- and rewriting `id` would
    re-point every audit row that ever referenced this account.

    Every non-target column is compared byte for byte, not just the three the
    plan named: a smuggling test that checks a subset proves only that the
    subset is safe."""
    target = _make_user(db_session, department="Engineering")
    before = {
        "id": target.id, "username": target.username, "email": target.email,
        "full_name": target.full_name, "password_hash": target.password_hash,
        "department": target.department, "is_active": target.is_active,
        "employee_ref": target.employee_ref, "helpdesk_ref": target.helpdesk_ref,
    }
    hijack_id = uuid.uuid4()

    _admin, headers = _login(client, db_session, username="muesc", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{target.id}",
        json={
            "role": "helpdesk",
            "id": str(hijack_id),
            "username": "hijacked",
            "email": "hijacked@northstar.example",
            "full_name": "Hijacked",
            "password_hash": "attacker-hash",
            "is_active": False,
            "department": "Finance",
            "employee_ref": "EMP-001",
            "helpdesk_ref": "HD-001",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = db_session.query(User).filter(User.id == before["id"]).one()
    assert row.role is Role.HELPDESK, "the one permitted field must still change"
    for field, original in before.items():
        assert getattr(row, field) == original, f"{field} was smuggled through the PATCH"
    assert db_session.query(User).filter(User.id == hijack_id).one_or_none() is None


def test_patching_an_unknown_user_is_404(client, db_session):
    """The `detail` is asserted, not just the status: FastAPI answers an
    absent ROUTE with 404 "Not Found" too, so a status-only assertion would
    pass just as happily against an endpoint that was never registered."""
    _admin, headers = _login(client, db_session, username="mu404", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{uuid.uuid4()}", json={"role": "helpdesk"}, headers=headers,
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "no such user"


def test_an_invalid_role_is_rejected_without_touching_the_row(client, db_session):
    target = _make_user(db_session)

    _admin, headers = _login(client, db_session, username="mubad", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{target.id}", json={"role": "godmode"}, headers=headers,
    )

    assert response.status_code == 422
    db_session.refresh(target)
    assert target.role is Role.EMPLOYEE
    assert db_session.query(AuditLog).filter(
        AuditLog.target_id == str(target.id),
    ).count() == 0, "a rejected request must not leave an audit row"


def test_an_invalid_clearance_is_rejected_without_touching_the_row(client, db_session):
    target = _make_user(db_session)

    _admin, headers = _login(client, db_session, username="mubadc", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/users/{target.id}",
        json={"clearance": "omniscient"}, headers=headers,
    )

    assert response.status_code == 422
    db_session.refresh(target)
    assert target.clearance is Clearance.STANDARD


def test_the_change_and_its_audit_row_share_one_transaction(committing_client, monkeypatch):
    """Spec 8: "PATCH /users/{id} writes both the change and its audit row in
    one transaction, and neither survives a rollback."

    The failure is injected AFTER `record_audit` has staged and flushed its
    row, which is what makes this discriminating in both directions. A handler
    that committed the role change before auditing would leave that change
    visible to the verifying connection; an audit helper that committed on its
    own connection (the way app/tracing/store.py deliberately does) would
    leave the audit row visible. Only a handler that stages both and commits
    once leaves nothing behind.

    Run against `committing_client`, not `client`: see the module docstring
    for why the ordinary fixture cannot fail this test."""
    from app.audit.service import record_audit as real_record_audit

    test_client, request_session = committing_client
    admin_name = f"mtxadm{uuid.uuid4().hex[:8]}"
    _hard_commit_user(
        username=admin_name, email=f"{admin_name}@northstar.example",
        full_name="Tx Admin", role=Role.ADMIN, clearance=Clearance.PRIVILEGED,
    )
    target_id = _hard_commit_user()

    resp = test_client.post(
        "/api/auth/login", json={"username": admin_name, "password": "Passw0rd!dev"},
    )
    assert resp.status_code == 200, resp.text
    headers = {"Authorization": f"Bearer {resp.json()['access_token']}"}

    class _AuditExploded(RuntimeError):
        pass

    def _audit_then_explode(db, **kwargs):
        real_record_audit(db, **kwargs)
        raise _AuditExploded("the transaction dies after the audit row is staged")

    monkeypatch.setattr("app.admin.router.record_audit", _audit_then_explode)

    with pytest.raises(_AuditExploded):
        test_client.patch(
            f"/api/admin/users/{target_id}",
            json={"role": "helpdesk", "clearance": "sensitive"},
            headers=headers,
        )

    monkeypatch.undo()
    request_session.rollback()

    # A second, independent connection: the only vantage point from which a
    # half-committed transaction is distinguishable from an aborted one.
    Session = get_sessionmaker()
    with Session() as verify:
        row = verify.query(User).filter(User.id == target_id).one()
        assert row.role is Role.EMPLOYEE, "the role change survived a rolled-back transaction"
        assert row.clearance is Clearance.STANDARD
        assert verify.query(AuditLog).filter(
            AuditLog.target_id == str(target_id),
        ).count() == 0, "an audit row survived the mutation it claims to record"


# ---- GET /users --------------------------------------------------------------------

def test_users_list_is_paginated_and_totals_the_whole_table(client, db_session):
    _admin, headers = _login(client, db_session, username="mulist", role=Role.ADMIN)

    body = client.get("/api/admin/users?limit=10", headers=headers).json()

    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["limit"] == 10
    assert body["offset"] == 0
    assert len(body["items"]) == 10
    # Exact, not `>= 0`: `total` must be the count of the whole table, so a
    # `total` computed from the page (or forgetting the seeded rows) fails.
    assert body["total"] == db_session.query(User).count()
    assert body["total"] >= 126


def test_users_pages_do_not_overlap_or_skip(client, db_session):
    """Without a stable sort key, `offset` silently returns overlapping or
    skipped pages -- the bug that makes a pager quietly lose rows."""
    _admin, headers = _login(client, db_session, username="mupage", role=Role.ADMIN)

    first = client.get("/api/admin/users?limit=5&offset=0", headers=headers).json()
    second = client.get("/api/admin/users?limit=5&offset=5", headers=headers).json()
    ten = client.get("/api/admin/users?limit=10&offset=0", headers=headers).json()

    assert [u["id"] for u in first["items"]] + [u["id"] for u in second["items"]] == [
        u["id"] for u in ten["items"]
    ]
    assert second["offset"] == 5
    usernames = [u["username"] for u in ten["items"]]
    assert usernames == sorted(usernames)


def test_an_over_large_user_limit_is_clamped_not_rejected(client, db_session):
    _admin, headers = _login(client, db_session, username="muclamp", role=Role.ADMIN)

    response = client.get("/api/admin/users?limit=100000", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert body["limit"] == 200
    # Exact: the cap is applied, and the page is otherwise the whole table.
    assert len(body["items"]) == min(200, body["total"])


def test_users_list_marks_the_shared_password_seed_accounts(client, db_session):
    """`dev_seed` marks the 125 accounts seeded from the EMP-xxx and HD-xxx
    profiles, which all share SEED_USER_PASSWORD (spec 5.6 item 4). It is
    derived from `employee_ref`/`helpdesk_ref` because those are exactly the
    columns `app/db/seed.py` sets on those two populations and on nothing
    else.

    The `admin` account is deliberately NOT flagged: seed.py creates it from
    ADMIN_PASSWORD (item 1), not the shared seed password, so flagging it
    would assert something untrue about its credentials. That boundary is
    asserted here rather than left implicit, because it is the one row where
    "seeded" and "shares the dev password" disagree.

    Counts are exact. `len(flagged) >= 1` would pass against a `dev_seed`
    that was hardcoded True."""
    _admin, headers = _login(client, db_session, username="museed", role=Role.ADMIN)

    body = client.get("/api/admin/users?limit=200", headers=headers).json()
    assert body["total"] <= 200, "this assertion needs the whole table in one page"

    by_name = {u["username"]: u for u in body["items"]}
    flagged = [u for u in body["items"] if u["dev_seed"]]
    assert len(flagged) == 125
    assert all(u["employee_ref"] or u["helpdesk_ref"] for u in flagged)
    assert len([u for u in flagged if u["employee_ref"]]) == 100
    assert len([u for u in flagged if u["helpdesk_ref"]]) == 25
    assert by_name["admin"]["dev_seed"] is False
    # The test's own admin, created here with no ref, is likewise unflagged.
    assert by_name["museed"]["dev_seed"] is False


def test_users_list_never_exposes_a_password_hash(client, db_session):
    """The serialiser is an explicit dict rather than a model dump precisely
    so a column cannot join the payload by being added to the table."""
    _admin, headers = _login(client, db_session, username="muhash", role=Role.ADMIN)

    body = client.get("/api/admin/users?limit=5", headers=headers).json()

    assert set(body["items"][0]) == {
        "id", "username", "email", "full_name", "role", "clearance",
        "department", "employee_ref", "helpdesk_ref", "is_active", "dev_seed",
    }


# ---- Lessons -----------------------------------------------------------------------

def test_deleting_a_lesson_archives_it_rather_than_removing_it(client, db_session):
    """Spec 4.2 and parent spec 20: lessons are archivable precisely so a bad
    one can be withdrawn from retrieval without destroying the record that it
    existed and was acted on."""
    lesson = _make_lesson(db_session)
    lesson_id = lesson.id

    _admin, headers = _login(client, db_session, username="mules", role=Role.ADMIN)
    response = client.delete(f"/api/admin/lessons/{lesson_id}", headers=headers)

    assert response.status_code == 200
    assert response.json() == {
        "id": str(lesson_id), "status": "archived", "archived": True,
    }

    # Queried back by id from a cleared identity map, not merely refreshed:
    # `refresh` on a deleted row raises, but a fresh query returning None is
    # the unambiguous statement that the row is gone.
    db_session.expire_all()
    row = db_session.query(Lesson).filter(Lesson.id == lesson_id).one_or_none()
    assert row is not None, "DELETE removed the row instead of archiving it"
    assert row.status is LessonStatus.ARCHIVED
    assert row.content_md == "body", "archiving must not touch the content"

    rows = db_session.query(AuditLog).filter(
        AuditLog.target_type == "lesson", AuditLog.target_id == str(lesson_id),
    ).all()
    assert len(rows) == 1
    assert rows[0].action == "lesson.archived", "not 'deleted' -- it was not deleted"


def test_an_archived_lesson_is_still_listed(client, db_session):
    """The record surviving is the point (spec 4.2), so the list must not
    filter archived lessons out -- an admin cannot review a withdrawal they
    cannot see."""
    lesson = _make_lesson(db_session)
    lesson_id = lesson.id

    _admin, headers = _login(client, db_session, username="mulist2", role=Role.ADMIN)
    assert client.delete(f"/api/admin/lessons/{lesson_id}", headers=headers).status_code == 200

    items = client.get("/api/admin/lessons?limit=200", headers=headers).json()["items"]
    listed = [i for i in items if i["id"] == str(lesson_id)]
    assert len(listed) == 1
    assert listed[0]["status"] == "archived"


def test_deleting_an_already_archived_lesson_is_idempotent(client, db_session):
    """DECISION: a repeat DELETE is idempotent (200, still archived), not a
    409. The verb states a desired end state, that end state already holds,
    and a panel whose delete button 409s on a double-click is worse than one
    that does nothing.

    The audit row is NOT suppressed, though: the second row records that an
    admin issued the request, which is a fact about the admin regardless of
    whether the row changed. `audit_log` records actions taken, and silently
    dropping one because it happened to be a no-op is how an audit trail
    starts lying by omission."""
    lesson = _make_lesson(db_session, status=LessonStatus.ARCHIVED)
    lesson_id = lesson.id

    _admin, headers = _login(client, db_session, username="muidem", role=Role.ADMIN)
    first = client.delete(f"/api/admin/lessons/{lesson_id}", headers=headers)
    second = client.delete(f"/api/admin/lessons/{lesson_id}", headers=headers)

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.json()["status"] == "archived"
    db_session.expire_all()
    assert db_session.query(Lesson).filter(Lesson.id == lesson_id).one().status is LessonStatus.ARCHIVED
    assert db_session.query(AuditLog).filter(
        AuditLog.target_id == str(lesson_id),
    ).count() == 2


def test_deleting_an_unknown_lesson_is_404(client, db_session):
    _admin, headers = _login(client, db_session, username="muled", role=Role.ADMIN)
    response = client.delete(f"/api/admin/lessons/{uuid.uuid4()}", headers=headers)
    # See test_patching_an_unknown_user_is_404 on why `detail` is asserted.
    assert response.status_code == 404
    assert response.json()["detail"] == "no such lesson"


def test_patching_a_lesson_updates_content_title_and_status_and_audits_it(client, db_session):
    lesson = _make_lesson(db_session)
    lesson_id = lesson.id

    _admin, headers = _login(client, db_session, username="mulpat", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/lessons/{lesson_id}",
        json={"content_md": "corrected body", "title": "Corrected", "status": "archived"},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"id": str(lesson_id), "status": "archived"}
    db_session.expire_all()
    row = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert row.content_md == "corrected body"
    assert row.title == "Corrected"
    assert row.status is LessonStatus.ARCHIVED

    rows = db_session.query(AuditLog).filter(
        AuditLog.target_type == "lesson", AuditLog.target_id == str(lesson_id),
    ).all()
    assert len(rows) == 1
    assert rows[0].action == "lesson.updated"
    assert rows[0].payload == {"status": "archived"}


def test_patching_a_lesson_cannot_change_its_provenance(client, db_session):
    """`category`, `file_path`, `ticket_id` and `created_by_run_id` say where a
    lesson came from. An admin editing the text of a lesson must not be able
    to re-attribute it to a different ticket or run: that is the chain that
    makes a lesson auditable at all."""
    lesson = _make_lesson(db_session)
    lesson_id = lesson.id
    before = {
        "category": lesson.category, "file_path": lesson.file_path,
        "ticket_id": lesson.ticket_id, "created_by_run_id": lesson.created_by_run_id,
        "confidence": lesson.confidence,
    }

    _admin, headers = _login(client, db_session, username="mulprov", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/lessons/{lesson_id}",
        json={
            "title": "Retitled", "category": "hijacked",
            "file_path": "knowledge/lessons/elsewhere.md",
            "ticket_id": str(uuid.uuid4()), "created_by_run_id": str(uuid.uuid4()),
            "confidence": "high",
        },
        headers=headers,
    )

    assert response.status_code == 200, response.text
    db_session.expire_all()
    row = db_session.query(Lesson).filter(Lesson.id == lesson_id).one()
    assert row.title == "Retitled", "the one permitted field must still change"
    for field, original in before.items():
        assert getattr(row, field) == original, f"{field} was smuggled through the PATCH"


def test_patching_an_unknown_lesson_is_404(client, db_session):
    _admin, headers = _login(client, db_session, username="mulp404", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/lessons/{uuid.uuid4()}", json={"title": "x"}, headers=headers,
    )
    # See test_patching_an_unknown_user_is_404 on why `detail` is asserted.
    assert response.status_code == 404
    assert response.json()["detail"] == "no such lesson"


def test_an_invalid_lesson_status_is_rejected_without_touching_the_row(client, db_session):
    lesson = _make_lesson(db_session)
    lesson_id = lesson.id

    _admin, headers = _login(client, db_session, username="mulbad", role=Role.ADMIN)
    response = client.patch(
        f"/api/admin/lessons/{lesson_id}", json={"status": "shredded"}, headers=headers,
    )

    assert response.status_code == 422
    db_session.expire_all()
    assert db_session.query(Lesson).filter(
        Lesson.id == lesson_id,
    ).one().status is LessonStatus.ACTIVE


def test_lessons_list_returns_the_row_with_its_exact_fields(client, db_session):
    """`lessons` is empty until Phase 9's learning loop writes to it, so a
    test that only asserted `total >= 0` over the empty table would pass
    against an endpoint that returned a constant. This one inserts a row it
    knows and asserts the serialised shape exactly."""
    lesson = _make_lesson(db_session, title="Known lesson", content_md="known body")

    _admin, headers = _login(client, db_session, username="mulem", role=Role.ADMIN)
    body = client.get("/api/admin/lessons?limit=200", headers=headers).json()

    assert set(body) == {"items", "total", "limit", "offset"}
    assert body["total"] == db_session.query(Lesson).count()
    match = [i for i in body["items"] if i["id"] == str(lesson.id)]
    assert len(match) == 1
    assert match[0] == {
        "id": str(lesson.id), "title": "Known lesson", "category": "vpn_network",
        "content_md": "known body", "status": "active", "confidence": "low",
        "ticket_id": None, "created_at": lesson.created_at.isoformat(),
    }


def test_an_over_large_lesson_limit_is_clamped_not_rejected(client, db_session):
    _admin, headers = _login(client, db_session, username="mulclamp", role=Role.ADMIN)
    response = client.get("/api/admin/lessons?limit=100000", headers=headers)
    assert response.status_code == 200
    assert response.json()["limit"] == 200


# ---- Authorization matrix ----------------------------------------------------------

# Every route this task adds, with a body where the verb needs one. The ids are
# random on purpose: authorization must be decided before the row is looked up,
# so a non-admin gets 403 rather than a 404 that leaks whether the id exists.
_ROUTES = [
    ("GET", "/api/admin/users", None),
    ("PATCH", f"/api/admin/users/{uuid.uuid4()}", {"role": "admin"}),
    ("GET", "/api/admin/lessons", None),
    ("PATCH", f"/api/admin/lessons/{uuid.uuid4()}", {"status": "archived"}),
    ("DELETE", f"/api/admin/lessons/{uuid.uuid4()}", None),
]


@pytest.mark.parametrize("method,path,body", _ROUTES)
@pytest.mark.parametrize("role", [Role.EMPLOYEE, Role.HELPDESK])
def test_a_logged_in_non_admin_is_refused(client, db_session, method, path, body, role):
    _user, headers = _login(
        client, db_session,
        username=f"mz{role.value}{abs(hash((method, path))) % 100000}", role=role,
    )
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403, f"{method} {path} as {role.value}: {response.text}"


@pytest.mark.parametrize("method,path,body", _ROUTES)
def test_a_guest_is_refused(client, method, path, body):
    """403, not 401: a guest is authenticated, just not authorized."""
    headers = _guest_login(client)
    response = client.request(method, path, headers=headers, json=body)
    assert response.status_code == 403, f"{method} {path} as guest: {response.text}"


@pytest.mark.parametrize("method,path,body", _ROUTES)
def test_an_anonymous_caller_is_refused(client, method, path, body):
    """401, not 403: there is no principal at all to authorize."""
    response = client.request(method, path, json=body)
    assert response.status_code == 401, f"{method} {path} anonymous: {response.text}"
