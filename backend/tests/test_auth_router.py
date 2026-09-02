from app.auth.security import create_access_token
from app.config import get_settings
from app.db.seed import seed


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
