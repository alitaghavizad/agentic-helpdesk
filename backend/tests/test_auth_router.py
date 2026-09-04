import threading
import time
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, Response

from app.auth.router import refresh as refresh_endpoint
from app.auth.security import create_access_token, create_refresh_token, hash_password
from app.config import get_settings
from app.db.models import Clearance, RefreshToken, Role, User
from app.db.seed import seed
from app.db.session import get_sessionmaker


def test_admin_login_succeeds(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    assert response.status_code == 200
    body = response.json()
    assert "access_token" in body
    assert "refresh_token" in response.cookies


def test_login_wrong_password_returns_generic_error(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": "not-the-password"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_login_nonexistent_user_returns_same_generic_error(client, db_session):
    response = client.post(
        "/api/auth/login", json={"username": "nobody", "password": "whatever"},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_guest_login_returns_token_without_user_row(client):
    response = client.post(
        "/api/auth/guest", json={"name": "Curious Visitor", "email": "visitor@example.com"},
    )
    assert response.status_code == 200
    assert "access_token" in response.json()
    assert "refresh_token" not in response.cookies


def test_me_returns_principal_matching_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    login_response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    access_token = login_response.json()["access_token"]
    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["role"] == "admin"
    assert body["kind"] == "user"
    # The seeded admin (app/db/seed.py) has full_name="Administrator" and no
    # employee_ref/helpdesk_ref -- this is what the frontend NavBar shows in
    # place of a raw user id, so it must be the real name, not None.
    assert body["username"] == settings.admin_username
    assert body["full_name"] == "Administrator"


def test_me_for_guest_has_no_username_but_has_the_given_name(client):
    # A guest is not a row in `users`, so username is honestly None rather
    # than invented -- full_name carries the guest's own self-reported name.
    guest_response = client.post(
        "/api/auth/guest", json={"name": "Curious Visitor", "email": "visitor@example.com"},
    )
    access_token = guest_response.json()["access_token"]
    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {access_token}"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["kind"] == "guest"
    assert body["username"] is None
    assert body["full_name"] == "Curious Visitor"


def test_refresh_issues_new_access_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    login_response = client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    old_access_token = login_response.json()["access_token"]
    refresh_response = client.post("/api/auth/refresh")
    assert refresh_response.status_code == 200
    new_access_token = refresh_response.json()["access_token"]
    assert new_access_token != old_access_token


def test_logout_revokes_refresh_token(client, db_session):
    seed(session=db_session)
    settings = get_settings()
    client.post(
        "/api/auth/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    )
    logout_response = client.post("/api/auth/logout")
    assert logout_response.status_code == 204
    refresh_response = client.post("/api/auth/refresh")
    assert refresh_response.status_code == 401


def test_me_without_token_is_unauthorized(client):
    response = client.get("/api/auth/me")
    assert response.status_code == 401


def test_me_with_token_missing_kind_and_role_is_unauthorized_not_500(client):
    # Deliberately omit "kind"/"role" to simulate a malformed/incomplete
    # access token — this must produce a clean 401, never an unhandled
    # KeyError -> 500.
    token = create_access_token({"sub": "x"})
    response = client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 401


def test_two_concurrent_refreshes_of_the_same_token_only_one_succeeds():
    """The identical TOCTOU shape phase 6's approval-decision race had:
    refresh() reads revoked_at, checks it in Python, then writes it. This
    forces a genuine overlap the same way test_approvals_service.py's
    concurrent-decisions test does: a raw session takes the row lock and
    holds it open while a second, genuinely concurrent call to the REAL
    refresh() endpoint function is made against the SAME token. With
    with_for_update()+populate_existing(), that second call blocks on the
    lock, then correctly sees the already-revoked row and 401s. Without
    the fix, its plain SELECT would not block, would read the still-
    revoked_at=None row, and would succeed in rotating an already-used
    token."""
    Session = get_sessionmaker()
    with Session() as setup:
        suffix = uuid.uuid4().hex[:8]
        user = User(
            username=f"refr{suffix}", email=f"refr{suffix}@northstar.example",
            full_name="Refresh Race", password_hash=hash_password("Passw0rd!dev"),
            role=Role.EMPLOYEE, clearance=Clearance.STANDARD, is_active=True,
        )
        setup.add(user)
        setup.commit()
        user_id = user.id

        raw_token, token_hash, expires_at = create_refresh_token(subject=str(user_id))
        setup.add(RefreshToken(user_id=user_id, token_hash=token_hash, expires_at=expires_at))
        setup.commit()

    lock_held = threading.Event()
    release_lock = threading.Event()

    def _hold_lock():
        with Session() as s:
            stored = (
                s.query(RefreshToken).filter_by(token_hash=token_hash)
                .populate_existing().with_for_update().one()
            )
            lock_held.set()
            release_lock.wait(timeout=15)
            stored.revoked_at = datetime.now(timezone.utc)
            s.commit()

    outcome: dict = {}

    def _try_refresh():
        with Session() as s:
            try:
                refresh_endpoint(response=Response(), db=s, refresh_token=raw_token)
                outcome["result"] = "succeeded"
            except HTTPException as exc:
                outcome["result"] = "rejected"
                outcome["status_code"] = exc.status_code

    try:
        holder = threading.Thread(target=_hold_lock, name="refresh-holder")
        holder.start()
        assert lock_held.wait(timeout=15), "the lock-holding thread never acquired the row lock"

        refresher = threading.Thread(target=_try_refresh, name="refresh-attempt")
        refresher.start()
        time.sleep(0.3)  # give the refresher a real chance to reach and block on the lock
        release_lock.set()
        holder.join(timeout=15)
        refresher.join(timeout=15)
        assert not holder.is_alive() and not refresher.is_alive(), "a thread never finished"

        assert outcome.get("result") == "rejected", (
            f"expected the concurrent refresh to be rejected once the token was "
            f"already revoked, got: {outcome}"
        )
        assert outcome.get("status_code") == 401
    finally:
        # By user_id, not just the original token_hash: if the race
        # succeeds (the pre-fix bug this test exists to catch), the second
        # call's _issue_tokens() mints and commits a SECOND RefreshToken
        # row for a rotated token this test never saw the hash of --
        # missing it here would leave a dangling FK and fail the DELETE
        # FROM users below with ForeignKeyViolation instead of failing the
        # actual assertion above.
        with Session() as cleanup:
            cleanup.query(RefreshToken).filter_by(user_id=user_id).delete()
            cleanup.query(User).filter_by(id=user_id).delete()
            cleanup.commit()
