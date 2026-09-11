"""Every way a bearer token can be wrong, and the 401 each one earns.

`get_current_principal` (app/deps.py) is the single gate in front of every
authenticated route, and its `type != "access"` and missing-claims branches
had no test reaching them at all -- they are what stops a refresh token, or
a hand-forged one, from being spent as an access token. A check nothing
exercises is a check nobody would notice losing.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import jwt

from app.auth.security import JWT_ALGORITHM, create_refresh_token
from app.config import get_settings


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_refresh_token_is_rejected_as_a_bearer_token(client):
    raw, _hash, _expires = create_refresh_token(subject=str(uuid.uuid4()))
    resp = client.get("/api/auth/me", headers=_bearer(raw))
    assert resp.status_code == 401
    assert resp.json()["detail"] == "Invalid token type"


def test_token_with_no_type_claim_is_rejected(client):
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {"sub": "x", "kind": "user", "role": "admin", "user_id": str(uuid.uuid4()),
         "iat": now, "exp": now + timedelta(minutes=15)},
        get_settings().jwt_secret, algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/api/admin/overview", headers=_bearer(forged))
    assert resp.status_code == 401


def test_access_token_missing_role_is_rejected(client):
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {"sub": "x", "type": "access", "kind": "user", "user_id": str(uuid.uuid4()),
         "iat": now, "exp": now + timedelta(minutes=15)},
        get_settings().jwt_secret, algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/api/admin/overview", headers=_bearer(forged))
    assert resp.status_code == 401


def test_expired_access_token_is_rejected(client):
    now = datetime.now(timezone.utc)
    expired = jwt.encode(
        {"sub": "x", "type": "access", "kind": "user", "role": "admin",
         "user_id": str(uuid.uuid4()),
         "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)},
        get_settings().jwt_secret, algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/api/admin/overview", headers=_bearer(expired))
    assert resp.status_code == 401


def test_token_signed_with_the_wrong_secret_is_rejected(client):
    now = datetime.now(timezone.utc)
    forged = jwt.encode(
        {"sub": "x", "type": "access", "kind": "user", "role": "admin",
         "user_id": str(uuid.uuid4()),
         "iat": now, "exp": now + timedelta(minutes=15)},
        "not-the-real-secret", algorithm=JWT_ALGORITHM,
    )
    resp = client.get("/api/admin/overview", headers=_bearer(forged))
    assert resp.status_code == 401


def test_algorithm_none_token_is_rejected(client):
    """The classic JWT bypass: re-sign with alg=none and hope the verifier
    trusts the header."""
    forged = jwt.encode(
        {"sub": "x", "type": "access", "kind": "user", "role": "admin",
         "user_id": str(uuid.uuid4())},
        key="", algorithm="none",
    )
    resp = client.get("/api/admin/overview", headers=_bearer(forged))
    assert resp.status_code == 401
