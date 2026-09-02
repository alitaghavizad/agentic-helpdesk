from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status
from pydantic import BaseModel, EmailStr

from app.auth.security import (
    create_access_token, create_refresh_token, decode_token,
    hash_password, hash_refresh_token, verify_password,
)
from app.db.models import RefreshToken, User
from app.deps import CurrentPrincipal, DbSession

router = APIRouter(prefix="/api/auth", tags=["auth"])

GENERIC_AUTH_ERROR = "Invalid username or password"
REFRESH_COOKIE_NAME = "refresh_token"
RefreshCookie = Annotated[str | None, Cookie(alias=REFRESH_COOKIE_NAME)]

# Fixed dummy hash so verify_password always runs a real bcrypt comparison,
# even for a nonexistent username — prevents timing-based user enumeration.
_DUMMY_HASH = hash_password("dummy-password-for-timing-normalization")


class LoginRequest(BaseModel):
    username: str
    password: str


class GuestRequest(BaseModel):
    name: str
    email: EmailStr


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class PrincipalResponse(BaseModel):
    kind: str
    user_id: str | None
    role: str
    clearance: str | None
    department: str | None
    employee_ref: str | None
    helpdesk_ref: str | None
    # A guest is not a row in `users`, so it has no username -- optional
    # rather than invented. full_name is set for both kinds (the user's
    # real name, or the guest's self-reported name) so a UI has one field
    # it can always show in place of a raw id.
    username: str | None
    full_name: str | None


def _claims_for_user(user: User) -> dict:
    return {
        "sub": str(user.id),
        "kind": "user",
        "user_id": str(user.id),
        "role": user.role.value,
        "clearance": user.clearance.value if user.clearance else None,
        "department": user.department,
        "employee_ref": user.employee_ref,
        "helpdesk_ref": user.helpdesk_ref,
        "username": user.username,
        "full_name": user.full_name,
    }


def _issue_tokens(db: DbSession, response: Response, claims: dict, subject: str) -> TokenResponse:
    access_token = create_access_token(claims)
    if claims["kind"] == "user":
        raw_refresh, token_hash, expires_at = create_refresh_token(subject=subject)
        db.add(RefreshToken(user_id=uuid.UUID(subject), token_hash=token_hash, expires_at=expires_at))
        db.commit()
        response.set_cookie(
            REFRESH_COOKIE_NAME, raw_refresh, httponly=True, samesite="strict",
            max_age=7 * 24 * 3600, path="/api/auth",
        )
    return TokenResponse(access_token=access_token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: DbSession) -> TokenResponse:
    user = db.query(User).filter_by(username=payload.username).one_or_none()
    password_ok = verify_password(
        payload.password, user.password_hash if user else _DUMMY_HASH
    )
    if user is None or not user.is_active or not password_ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_AUTH_ERROR)
    return _issue_tokens(db, response, _claims_for_user(user), subject=str(user.id))


@router.post("/guest", response_model=TokenResponse)
def guest_login(payload: GuestRequest, response: Response, db: DbSession) -> TokenResponse:
    claims = {
        "sub": payload.email,
        "kind": "guest",
        "user_id": None,
        "role": "guest",
        "clearance": None,
        "department": None,
        "employee_ref": None,
        "helpdesk_ref": None,
        "guest_name": payload.name,
        "guest_email": payload.email,
        # No `users` row exists for a guest, so no username -- full_name is
        # the guest's own self-reported name, which is real and honest.
        "username": None,
        "full_name": payload.name,
    }
    return _issue_tokens(db, response, claims, subject=payload.email)


@router.post("/refresh", response_model=TokenResponse)
def refresh(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> TokenResponse:
    if refresh_token is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    try:
        claims = decode_token(refresh_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    if claims.get("type") != "refresh":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    token_hash = hash_refresh_token(refresh_token)
    stored = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
    if stored is None or stored.revoked_at is not None or stored.expires_at < datetime.now(timezone.utc):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
    user = db.get(User, stored.user_id)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Refresh token no longer valid")
    stored.revoked_at = datetime.now(timezone.utc)
    db.commit()
    return _issue_tokens(db, response, _claims_for_user(user), subject=str(user.id))


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(response: Response, db: DbSession, refresh_token: RefreshCookie = None) -> None:
    if refresh_token:
        token_hash = hash_refresh_token(refresh_token)
        stored = db.query(RefreshToken).filter_by(token_hash=token_hash).one_or_none()
        if stored is not None:
            stored.revoked_at = datetime.now(timezone.utc)
            db.commit()
    response.delete_cookie(REFRESH_COOKIE_NAME, path="/api/auth")


@router.get("/me", response_model=PrincipalResponse)
def me(principal: CurrentPrincipal) -> PrincipalResponse:
    return PrincipalResponse(
        kind=principal.kind, user_id=principal.user_id, role=principal.role,
        clearance=principal.clearance, department=principal.department,
        employee_ref=principal.employee_ref, helpdesk_ref=principal.helpdesk_ref,
        username=principal.username, full_name=principal.full_name,
    )
