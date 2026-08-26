from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.auth.security import decode_token
from app.db.session import get_db
from app.rbac.policy import Principal

DbSession = Annotated[Session, Depends(get_db)]


def get_current_principal(
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        claims = decode_token(token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    if claims.get("type") != "access":
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid token type")
    if "kind" not in claims or "role" not in claims:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired token")
    return Principal(
        kind=claims["kind"],
        user_id=claims.get("user_id"),
        role=claims["role"],
        clearance=claims.get("clearance"),
        department=claims.get("department"),
        employee_ref=claims.get("employee_ref"),
        helpdesk_ref=claims.get("helpdesk_ref"),
        guest_name=claims.get("guest_name"),
        guest_email=claims.get("guest_email"),
    )


CurrentPrincipal = Annotated[Principal, Depends(get_current_principal)]


def require_role(*allowed: str):
    def _check(principal: CurrentPrincipal) -> Principal:
        if principal.role not in allowed:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient role")
        return principal
    return _check
